from __future__ import annotations

import json
import re
from pathlib import Path

from artifact_manager import ArtifactManager
from command_policy import CommandPolicyError
from context_builder import ContextBuilder
from failure_classifier import FailureClassifier
from llm_client import LLMSettings
from llm_plan_service import LocalLLMPlanService
from llm_review_service import LocalLLMReviewService
from models import (
    ApprovalMode,
    ArtifactReference,
    ChangePreview,
    CommandPolicyMode,
    ExecutionResult,
    FailureClassification,
    LLMStructuredResult,
    Mode,
    Plan,
    PhaseResult,
    ProjectMemoryContext,
    RepoIndexSummary,
    RepairSummary,
    StepResult,
    StepStatus,
    StepType,
    TaskAction,
    TaskContextPackage,
    TaskInterpretation,
    TaskResult,
    VerificationConfig,
    VerificationProfile,
)
from permission_manager import PermissionDeniedError, PermissionManager
from phase_policy import classify_plan_phases
from planner import Planner
from preview_manager import PreviewManager
from repair_manager import RepairManager
from repo_indexer import RepoIndexer
from scope_manager import ScopeManager, ScopeViolationError
from snapshot_manager import SnapshotManager
from terminal_runner import TerminalRunner
from verification_manager import VerificationManager
from workspace_manager import WorkspaceManager


class Orchestrator:
    def __init__(
        self, project_root: str, llm_settings: LLMSettings | None = None
    ) -> None:
        self.project_root = str(Path(project_root).resolve())
        self.planner = Planner()
        self.local_llm_plan_service = LocalLLMPlanService(llm_settings)
        self.local_llm_review_service = LocalLLMReviewService(llm_settings)
        self.context_builder = ContextBuilder()
        self.failure_classifier = FailureClassifier()
        self.repair_manager = RepairManager()

    def build_plan_with_local_llm(
        self,
        mode: Mode,
        interpretation: TaskInterpretation,
        scope_manager: ScopeManager,
        verification: VerificationConfig,
        repo_index_summary: RepoIndexSummary | None,
        project_memory: ProjectMemoryContext | None,
        artifact_manager: ArtifactManager,
        artifact_references: list[ArtifactReference],
    ) -> tuple[Plan | None, LLMStructuredResult | None, LLMStructuredResult | None]:
        deterministic_plan = self.planner.build_plan(
            mode,
            interpretation,
            scope_manager,
            verification,
            repo_index_summary,
        )
        if mode == Mode.CONVERSATION or not self.local_llm_plan_service.enabled():
            return deterministic_plan, None, None

        llm_plan, llm_summary = self.local_llm_plan_service.generate_plan(
            artifact_manager=artifact_manager,
            interpretation=interpretation,
            scope_manager=scope_manager,
            verification=verification,
            repo_index_summary=repo_index_summary,
            project_memory=project_memory,
        )
        if llm_summary is not None:
            for artifact in llm_summary.artifact_references:
                if not any(
                    existing.path == artifact.path for existing in artifact_references
                ):
                    artifact_references.append(artifact)
        accepted_plan = llm_plan or deterministic_plan
        llm_review_summary = None
        if accepted_plan is not None and self.local_llm_review_service.enabled():
            llm_review_summary = self.local_llm_review_service.review_plan(
                artifact_manager=artifact_manager,
                interpretation=interpretation,
                scope_manager=scope_manager,
                verification=verification,
                repo_index_summary=repo_index_summary,
                project_memory=project_memory,
                plan=accepted_plan,
            )
            if llm_review_summary is not None:
                for artifact in llm_review_summary.artifact_references:
                    if not any(
                        existing.path == artifact.path
                        for existing in artifact_references
                    ):
                        artifact_references.append(artifact)
        return accepted_plan, llm_summary, llm_review_summary

    def _handle_post_execution_failure(
        self,
        artifact_manager: ArtifactManager,
        artifact_references: list[ArtifactReference],
        auto_repair: bool,
        auto_repair_attempts: int,
        initial_execution_result: ExecutionResult,
        step_results: list[StepResult],
        workspace: WorkspaceManager,
        terminal: TerminalRunner,
        verification_manager: VerificationManager,
        interpretation: TaskInterpretation,
        verification: VerificationConfig,
        repo_index_summary: RepoIndexSummary | None,
        scope_manager: ScopeManager,
    ) -> tuple[ExecutionResult, FailureClassification | None, RepairSummary | None]:
        if initial_execution_result.success:
            return initial_execution_result, None, None
        if "not approved" in initial_execution_result.message.lower():
            return initial_execution_result, None, None

        failure_classification = self.failure_classifier.classify(
            initial_execution_result.message,
            step_results,
            workspace.blocked_actions,
            terminal.commands_run,
        )
        failure_classification = self._persist_failure_report(
            artifact_manager,
            artifact_references,
            failure_classification,
            initial_execution_result,
            step_results,
        )
        repair_summary = self.repair_manager.build_repair_summary(
            auto_repair_enabled=auto_repair,
            failure=failure_classification,
            interpretation=interpretation,
            verification=verification,
            repo_index_summary=repo_index_summary,
            scope_manager=scope_manager,
            repair_attempt_limit=auto_repair_attempts,
        )
        repair_summary = self._persist_repair_plan(
            artifact_manager,
            artifact_references,
            repair_summary,
        )
        if auto_repair and repair_summary.eligible:
            repair_summary = self.repair_manager.attempt_repair(
                repair_summary=repair_summary,
                failure=failure_classification,
                interpretation=interpretation,
                verification=verification,
                workspace=workspace,
                terminal=terminal,
                verification_manager=verification_manager,
                max_attempts=auto_repair_attempts,
            )
            repair_summary = self._persist_repair_result(
                artifact_manager,
                artifact_references,
                repair_summary,
            )
            final_execution_result = (
                repair_summary.repair_execution_result or initial_execution_result
            )
            if not final_execution_result.success:
                repair_summary.post_repair_failure_classification = (
                    self.failure_classifier.classify(
                        final_execution_result.message,
                        repair_summary.repair_step_results,
                        workspace.blocked_actions,
                        terminal.commands_run,
                    )
                )
            return final_execution_result, failure_classification, repair_summary

        return initial_execution_result, failure_classification, repair_summary

    def _build_repo_index(
        self,
        build_repo_index: bool,
        artifact_manager: ArtifactManager,
        artifact_references: list[ArtifactReference],
        workspace: WorkspaceManager,
        scope_manager: ScopeManager,
    ) -> RepoIndexSummary | None:
        if not build_repo_index:
            return None

        indexer = RepoIndexer(self.project_root, workspace, scope_manager)
        summary, payload = indexer.build_summary()
        artifact_reference = artifact_manager.save_json(
            artifact_manager.build_filename("repo_index"),
            payload,
        )
        summary.artifact_reference = artifact_reference
        artifact_references.append(artifact_reference)
        return summary

    def _print_change_preview(self, change_preview: ChangePreview) -> None:
        print("\nChange preview:")
        writes = (
            ", ".join(change_preview.intended_file_writes)
            if change_preview.intended_file_writes
            else "none"
        )
        commands = (
            ", ".join(change_preview.intended_commands)
            if change_preview.intended_commands
            else "none"
        )
        steps = (
            ", ".join(change_preview.relevant_steps)
            if change_preview.relevant_steps
            else "none"
        )
        print(f"- intended file writes: {writes}")
        print(f"- intended commands: {commands}")
        print(f"- relevant steps: {steps}")
        for file_change in change_preview.file_changes[:2]:
            print(f"- file preview: {file_change.path} ({file_change.action})")
            if file_change.blocked:
                print(f"  blocked: {file_change.summary}")
            elif file_change.diff_preview:
                print(f"  diff: {file_change.diff_preview}")

    def _build_phase_results(self, step_results: list[StepResult]) -> list[PhaseResult]:
        return self._build_phase_results_with_policy(step_results, [])

    def _build_phase_results_with_policy(
        self,
        step_results: list[StepResult],
        phase_policies,
    ) -> list[PhaseResult]:
        policy_map = {policy.phase: policy for policy in phase_policies}
        phases: list[str] = []
        for step in step_results:
            if step.phase not in phases:
                phases.append(step.phase)

        phase_results: list[PhaseResult] = []
        for phase in phases:
            phase_steps = [step for step in step_results if step.phase == phase]
            if any(step.status == StepStatus.FAILED for step in phase_steps):
                status = StepStatus.FAILED
                message = f"Phase '{phase}' failed."
            elif any(
                step.status == StepStatus.SKIPPED for step in phase_steps
            ) and not any(step.status == StepStatus.COMPLETED for step in phase_steps):
                status = StepStatus.SKIPPED
                message = f"Phase '{phase}' was skipped."
            elif all(step.status == StepStatus.COMPLETED for step in phase_steps):
                status = StepStatus.COMPLETED
                message = f"Phase '{phase}' completed."
            else:
                status = StepStatus.SKIPPED
                message = f"Phase '{phase}' stopped before completion."

            verification_outcomes = [
                step.details
                for step in phase_steps
                if step.step_type == StepType.VERIFICATION and step.details
            ]
            phase_results.append(
                PhaseResult(
                    phase=phase,
                    status=status,
                    message=message,
                    classification=(
                        policy_map[phase].classification.value
                        if phase in policy_map
                        else None
                    ),
                    approval_required=(
                        policy_map[phase].approval_required
                        if phase in policy_map
                        else True
                    ),
                    auto_ran=(
                        policy_map[phase].auto_ran if phase in policy_map else False
                    ),
                    approval_reason=(
                        policy_map[phase].reason if phase in policy_map else ""
                    ),
                    step_ids=[step.step_id for step in phase_steps],
                    verification_outcomes=verification_outcomes,
                )
            )
        return phase_results

    def _request_phase_approval(self, phase: str) -> bool:
        print(f"\nApprove phase '{phase}'? (y/n): ", end="")
        response = input().strip().lower()
        return response == "y"

    def _persist_failure_report(
        self,
        artifact_manager: ArtifactManager,
        artifact_references: list[ArtifactReference],
        failure: FailureClassification,
        execution_result: ExecutionResult,
        step_results: list[StepResult],
    ) -> FailureClassification:
        artifact = artifact_manager.save_json(
            artifact_manager.build_filename("failure_report"),
            {
                "failure_classification": failure.to_dict(),
                "execution_result": execution_result.to_dict(),
                "step_results": [step.to_dict() for step in step_results],
            },
        )
        artifact_references.append(artifact)
        failure.artifact_reference = artifact
        return failure

    def _persist_repair_plan(
        self,
        artifact_manager: ArtifactManager,
        artifact_references: list[ArtifactReference],
        repair_summary: RepairSummary,
    ) -> RepairSummary:
        if repair_summary.repair_plan is None:
            return repair_summary
        artifact = artifact_manager.save_json(
            artifact_manager.build_filename("repair_plan"),
            repair_summary.repair_plan.to_dict(),
        )
        artifact_references.append(artifact)
        repair_summary.artifact_references.append(artifact)
        return repair_summary

    def _persist_repair_result(
        self,
        artifact_manager: ArtifactManager,
        artifact_references: list[ArtifactReference],
        repair_summary: RepairSummary,
    ) -> RepairSummary:
        artifact = artifact_manager.save_json(
            artifact_manager.build_filename("repair_result"),
            repair_summary.to_dict(),
        )
        artifact_references.append(artifact)
        repair_summary.artifact_references.append(artifact)
        return repair_summary

    def _build_result(
        self,
        mode: Mode,
        permissions,
        approval_mode: ApprovalMode,
        command_policy: CommandPolicyMode,
        scope_manager: ScopeManager,
        verification: VerificationConfig,
        interpretation: TaskInterpretation,
        repo_index_summary: RepoIndexSummary | None,
        change_preview: ChangePreview | None,
        llm_summary: LLMStructuredResult | None,
        llm_review_summary: LLMStructuredResult | None,
        plan: Plan | None,
        phase_policies,
        context_package: TaskContextPackage | None,
        workspace: WorkspaceManager,
        terminal: TerminalRunner,
        artifact_references: list[ArtifactReference],
        phase_results: list[PhaseResult],
        step_results: list[StepResult],
        initial_execution_result: ExecutionResult | None,
        failure_classification: FailureClassification | None,
        repair_summary: RepairSummary | None,
        final_execution_result: ExecutionResult,
        snapshot_reference,
    ) -> TaskResult:
        combined_artifacts = list(artifact_references)
        for command in terminal.commands_run:
            if command.artifact_reference is not None and not any(
                existing.path == command.artifact_reference.path
                for existing in combined_artifacts
            ):
                combined_artifacts.append(command.artifact_reference)
        if repair_summary is not None:
            for artifact in repair_summary.artifact_references:
                if not any(
                    existing.path == artifact.path for existing in combined_artifacts
                ):
                    combined_artifacts.append(artifact)

        return TaskResult(
            task_interpretation=interpretation,
            selected_mode=mode,
            permissions=permissions,
            approval_mode=approval_mode,
            command_policy=command_policy,
            effective_scope=scope_manager.describe_effective_scope(),
            protected_paths=scope_manager.protected_path_labels(),
            verification=verification,
            context_package=context_package,
            repo_index_summary=repo_index_summary,
            change_preview=change_preview,
            llm_summary=llm_summary,
            llm_review_summary=llm_review_summary,
            plan=plan,
            phase_policies=phase_policies,
            phase_results=phase_results,
            step_results=step_results,
            blocked_actions=workspace.blocked_actions,
            files_read=workspace.files_read,
            files_modified=workspace.files_modified,
            commands_run=terminal.commands_run,
            artifact_references=combined_artifacts,
            initial_execution_result=initial_execution_result,
            failure_classification=failure_classification,
            repair_summary=repair_summary,
            final_execution_result=final_execution_result,
            execution_result=final_execution_result,
            snapshot_reference=snapshot_reference,
        )

    def _attach_result_artifact(
        self,
        artifact_manager: ArtifactManager,
        artifact_references: list[ArtifactReference],
        result: TaskResult,
    ) -> None:
        artifact = artifact_manager.save_json(
            artifact_manager.build_filename("result"),
            result.to_dict(),
        )
        artifact_references.append(artifact)
        if not any(
            existing.path == artifact.path for existing in result.artifact_references
        ):
            result.artifact_references.append(artifact)
        if result.context_package is not None and not any(
            existing.path == artifact.path
            for existing in result.context_package.artifact_references
        ):
            result.context_package.artifact_references.append(artifact)

    def _handle_conversation(
        self,
        workspace: WorkspaceManager,
        interpretation: TaskInterpretation,
        repo_index_summary: RepoIndexSummary | None,
    ) -> ExecutionResult:
        if interpretation.target_path:
            content = workspace.read_text(interpretation.target_path)
            details = {
                "analysis": self._summarize_file(interpretation.target_path, content)
            }
            if repo_index_summary is not None:
                details["repo_index_note"] = self._conversation_repo_note(
                    repo_index_summary
                )
            return ExecutionResult(
                success=True, message="Analysis completed.", details=details
            )

        files = workspace.list_files()
        details = {
            "analysis": (
                "Conversation mode is read-only. No specific file target was detected, "
                f"and the accessible workspace currently contains {len(files)} file(s)."
            )
        }
        if repo_index_summary is not None:
            details["repo_index_note"] = self._conversation_repo_note(
                repo_index_summary
            )
        return ExecutionResult(
            success=True, message="Analysis completed.", details=details
        )

    def _execute_plan(
        self,
        plan: Plan,
        phase_policies,
        scope_manager: ScopeManager,
        workspace: WorkspaceManager,
        terminal: TerminalRunner,
        snapshots: SnapshotManager,
        verification_manager: VerificationManager,
        interpretation: TaskInterpretation,
        verification: VerificationConfig,
        approval_mode: ApprovalMode,
    ) -> tuple[ExecutionResult, list[StepResult]]:
        if interpretation.action == TaskAction.CREATE_FILE:
            return self._execute_create_file_plan(
                plan,
                phase_policies,
                scope_manager,
                workspace,
                verification_manager,
                interpretation,
                verification,
                approval_mode,
            )
        if interpretation.action == TaskAction.MODIFY_FILE:
            return self._execute_modify_file_plan(
                plan,
                phase_policies,
                scope_manager,
                workspace,
                verification_manager,
                interpretation,
                verification,
                approval_mode,
            )
        if interpretation.action == TaskAction.RUN_COMMAND:
            return self._execute_command_plan(
                plan,
                phase_policies,
                terminal,
                verification_manager,
                interpretation,
                verification,
                approval_mode,
            )
        if interpretation.action == TaskAction.RESTORE_SNAPSHOT:
            return self._execute_restore_plan(
                plan,
                phase_policies,
                scope_manager,
                workspace,
                snapshots,
                verification_manager,
                interpretation,
                verification,
                approval_mode,
            )

        step_results = [self._unsupported_step_result(step) for step in plan.steps]
        return (
            ExecutionResult(
                success=False,
                message=(
                    "This Phase 4 foundation can safely plan any task, but IMPLEMENT mode currently "
                    "supports only file creation, shell command execution, and snapshot restore."
                ),
                details={},
            ),
            step_results,
        )

    def _execute_create_file_plan(
        self,
        plan: Plan,
        phase_policies,
        scope_manager: ScopeManager,
        workspace: WorkspaceManager,
        verification_manager: VerificationManager,
        interpretation: TaskInterpretation,
        verification: VerificationConfig,
        approval_mode: ApprovalMode,
    ) -> tuple[ExecutionResult, list[StepResult]]:
        assert interpretation.target_path is not None
        assert interpretation.content is not None
        step_results: list[StepResult] = []
        approved_phases: set[str] = set()

        try:
            if not self._approve_phase_if_needed(
                approval_mode, plan.steps[0].phase, approved_phases, phase_policies
            ):
                return self._declined_phase_result(
                    plan, step_results, plan.steps[0].phase
                )
            target = workspace.resolve_path(interpretation.target_path)
            scope_manager.enforce_write(target)
            exists = target.exists()
            step_results.append(
                self._completed_step(
                    plan.steps[0],
                    "Target path inspected.",
                    {
                        "target_path": interpretation.target_path,
                        "already_exists": exists,
                    },
                )
            )

            if not self._approve_phase_if_needed(
                approval_mode, plan.steps[1].phase, approved_phases, phase_policies
            ):
                return self._declined_phase_result(
                    plan, step_results, plan.steps[1].phase
                )
            workspace.write_text(interpretation.target_path, interpretation.content)
            step_results.append(
                self._completed_step(
                    plan.steps[1],
                    "Requested file content was written.",
                    {"target_path": interpretation.target_path},
                )
            )

            if not self._approve_phase_if_needed(
                approval_mode, plan.steps[2].phase, approved_phases, phase_policies
            ):
                return self._declined_phase_result(
                    plan, step_results, plan.steps[2].phase
                )
            verification_step = self._run_verification_step(
                step=plan.steps[2],
                action=interpretation.action,
                target_path=interpretation.target_path,
                expected_content=interpretation.content,
                verification=verification,
                verification_manager=verification_manager,
            )
            step_results.append(verification_step)
            success = verification_step.status != StepStatus.FAILED
            message = (
                f"Created '{interpretation.target_path}'."
                if success
                else f"Created '{interpretation.target_path}', but verification failed."
            )
            return (
                ExecutionResult(
                    success=success, message=message, details=verification_step.details
                ),
                step_results,
            )
        except ScopeViolationError as error:
            workspace.record_blocked_action(error.to_blocked_action())
            return self._handle_step_failure(plan, step_results, error)
        except Exception as error:
            return self._handle_step_failure(plan, step_results, error)

    def _execute_modify_file_plan(
        self,
        plan: Plan,
        phase_policies,
        scope_manager: ScopeManager,
        workspace: WorkspaceManager,
        verification_manager: VerificationManager,
        interpretation: TaskInterpretation,
        verification: VerificationConfig,
        approval_mode: ApprovalMode,
    ) -> tuple[ExecutionResult, list[StepResult]]:
        assert interpretation.target_path is not None
        assert interpretation.content is not None
        step_results: list[StepResult] = []
        approved_phases: set[str] = set()

        try:
            if not self._approve_phase_if_needed(
                approval_mode, plan.steps[0].phase, approved_phases, phase_policies
            ):
                return self._declined_phase_result(
                    plan, step_results, plan.steps[0].phase
                )
            target = workspace.resolve_path(interpretation.target_path)
            scope_manager.enforce_write(target)
            existed_before = target.exists()
            previous_content = (
                workspace.read_text(interpretation.target_path)
                if existed_before
                else None
            )
            step_results.append(
                self._completed_step(
                    plan.steps[0],
                    "Existing file state inspected.",
                    {
                        "target_path": interpretation.target_path,
                        "already_exists": existed_before,
                        "previous_content_available": previous_content is not None,
                    },
                )
            )

            if not self._approve_phase_if_needed(
                approval_mode, plan.steps[1].phase, approved_phases, phase_policies
            ):
                return self._declined_phase_result(
                    plan, step_results, plan.steps[1].phase
                )
            workspace.write_text(interpretation.target_path, interpretation.content)
            step_results.append(
                self._completed_step(
                    plan.steps[1],
                    "Requested file content was overwritten.",
                    {
                        "target_path": interpretation.target_path,
                        "created_new_file": not existed_before,
                    },
                )
            )

            if not self._approve_phase_if_needed(
                approval_mode, plan.steps[2].phase, approved_phases, phase_policies
            ):
                return self._declined_phase_result(
                    plan, step_results, plan.steps[2].phase
                )
            verification_step = self._run_verification_step(
                step=plan.steps[2],
                action=interpretation.action,
                target_path=interpretation.target_path,
                expected_content=interpretation.content,
                verification=verification,
                verification_manager=verification_manager,
            )
            step_results.append(verification_step)
            success = verification_step.status != StepStatus.FAILED
            message = (
                f"Modified '{interpretation.target_path}'."
                if success
                else f"Modified '{interpretation.target_path}', but verification failed."
            )
            return (
                ExecutionResult(
                    success=success, message=message, details=verification_step.details
                ),
                step_results,
            )
        except ScopeViolationError as error:
            workspace.record_blocked_action(error.to_blocked_action())
            return self._handle_step_failure(plan, step_results, error)
        except Exception as error:
            return self._handle_step_failure(plan, step_results, error)

    def _execute_command_plan(
        self,
        plan: Plan,
        phase_policies,
        terminal: TerminalRunner,
        verification_manager: VerificationManager,
        interpretation: TaskInterpretation,
        verification: VerificationConfig,
        approval_mode: ApprovalMode,
    ) -> tuple[ExecutionResult, list[StepResult]]:
        assert interpretation.command is not None
        step_results: list[StepResult] = []
        approved_phases: set[str] = set()

        try:
            if not self._approve_phase_if_needed(
                approval_mode, plan.steps[0].phase, approved_phases, phase_policies
            ):
                return self._declined_phase_result(
                    plan, step_results, plan.steps[0].phase
                )
            step_results.append(
                self._completed_step(
                    plan.steps[0],
                    "Command context confirmed.",
                    {
                        "command": interpretation.command,
                        "project_root": self.project_root,
                    },
                )
            )

            if not self._approve_phase_if_needed(
                approval_mode, plan.steps[1].phase, approved_phases, phase_policies
            ):
                return self._declined_phase_result(
                    plan, step_results, plan.steps[1].phase
                )
            result = terminal.run(interpretation.command)
            execution_status = (
                StepStatus.COMPLETED if result.success else StepStatus.FAILED
            )
            step_results.append(
                StepResult(
                    step_id=plan.steps[1].id,
                    title=plan.steps[1].title,
                    step_type=plan.steps[1].step_type,
                    status=execution_status,
                    message=result.summary,
                    phase=plan.steps[1].phase,
                    details=result.to_dict(),
                )
            )
            if not result.success:
                step_results.extend(self._skip_remaining_steps(plan.steps[2:]))
                return (
                    ExecutionResult(
                        success=False,
                        message="Command execution failed.",
                        details=result.to_dict(),
                    ),
                    step_results,
                )

            if not self._approve_phase_if_needed(
                approval_mode, plan.steps[2].phase, approved_phases, phase_policies
            ):
                return self._declined_phase_result(
                    plan, step_results, plan.steps[2].phase
                )
            verification_step = self._run_verification_step(
                step=plan.steps[2],
                action=interpretation.action,
                target_path=None,
                expected_content=None,
                verification=verification,
                verification_manager=verification_manager,
                command_result=result,
            )
            step_results.append(verification_step)
            success = verification_step.status != StepStatus.FAILED
            message = (
                "Command executed."
                if success
                else "Command executed, but verification failed."
            )
            details = (
                verification_step.details
                if verification_step.details
                else result.to_dict()
            )
            return (
                ExecutionResult(success=success, message=message, details=details),
                step_results,
            )
        except Exception as error:
            return self._handle_step_failure(plan, step_results, error)

    def _execute_restore_plan(
        self,
        plan: Plan,
        phase_policies,
        scope_manager: ScopeManager,
        workspace: WorkspaceManager,
        snapshots: SnapshotManager,
        verification_manager: VerificationManager,
        interpretation: TaskInterpretation,
        verification: VerificationConfig,
        approval_mode: ApprovalMode,
    ) -> tuple[ExecutionResult, list[StepResult]]:
        step_results: list[StepResult] = []
        approved_phases: set[str] = set()

        try:
            if not self._approve_phase_if_needed(
                approval_mode, plan.steps[0].phase, approved_phases, phase_policies
            ):
                return self._declined_phase_result(
                    plan, step_results, plan.steps[0].phase
                )
            scope_manager.enforce_full_workspace_write("restore")
            reference = (
                snapshots.latest_snapshot()
                if interpretation.snapshot_id is None
                else snapshots._reference_for(interpretation.snapshot_id)
            )
            step_results.append(
                self._completed_step(
                    plan.steps[0], "Snapshot source identified.", reference.to_dict()
                )
            )

            if not self._approve_phase_if_needed(
                approval_mode, plan.steps[1].phase, approved_phases, phase_policies
            ):
                return self._declined_phase_result(
                    plan, step_results, plan.steps[1].phase
                )
            restored = snapshots.restore_snapshot(interpretation.snapshot_id)
            step_results.append(
                self._completed_step(
                    plan.steps[1],
                    "Workspace restored from snapshot.",
                    restored.to_dict(),
                )
            )

            if not self._approve_phase_if_needed(
                approval_mode, plan.steps[2].phase, approved_phases, phase_policies
            ):
                return self._declined_phase_result(
                    plan, step_results, plan.steps[2].phase
                )
            verification_step = self._run_verification_step(
                step=plan.steps[2],
                action=interpretation.action,
                target_path=None,
                expected_content=None,
                verification=verification,
                verification_manager=verification_manager,
            )
            step_results.append(verification_step)
            success = verification_step.status != StepStatus.FAILED
            message = (
                f"Restored snapshot '{restored.snapshot_id}'."
                if success
                else f"Restored snapshot '{restored.snapshot_id}', but verification failed."
            )
            details = (
                verification_step.details
                if verification_step.details
                else restored.to_dict()
            )
            return (
                ExecutionResult(success=success, message=message, details=details),
                step_results,
            )
        except ScopeViolationError as error:
            workspace.record_blocked_action(error.to_blocked_action())
            return self._handle_step_failure(plan, step_results, error)
        except Exception as error:
            return self._handle_step_failure(plan, step_results, error)

    def _run_verification_step(
        self,
        step,
        action: TaskAction,
        target_path: str | None,
        expected_content: str | None,
        verification: VerificationConfig,
        verification_manager: VerificationManager,
        command_result=None,
        cancellation_event=None,
    ) -> StepResult:
        if verification.profile == VerificationProfile.NONE:
            return StepResult(
                step_id=step.id,
                title=step.title,
                step_type=step.step_type,
                status=StepStatus.SKIPPED,
                message="Verification profile is 'none'; no post-execution verification was run.",
                phase=step.phase,
            )

        if verification.profile == VerificationProfile.BASIC:
            if (
                action in {TaskAction.CREATE_FILE, TaskAction.MODIFY_FILE}
                and target_path is not None
                and expected_content is not None
            ):
                verification_result = verification_manager.verify_file_write(
                    target_path,
                    expected_content,
                    cancellation_event=cancellation_event,
                )
                success = bool(
                    verification_result["exists"]
                    and verification_result["content_matches"]
                    and verification_result.get("configured_commands_passed", True)
                )
                status = StepStatus.COMPLETED if success else StepStatus.FAILED
                return StepResult(
                    step_id=step.id,
                    title=step.title,
                    step_type=step.step_type,
                    status=status,
                    message=(
                        "Basic verification completed."
                        if status == StepStatus.COMPLETED
                        else "Basic verification failed."
                    ),
                    phase=step.phase,
                    details=verification_result,
                )
            if action == TaskAction.RUN_COMMAND and command_result is not None:
                verification_result = verification_manager.verify_command_success(
                    command_result,
                    cancellation_event=cancellation_event,
                )
                success = bool(
                    verification_result["command_success"]
                    and verification_result.get("configured_commands_passed", True)
                )
                status = StepStatus.COMPLETED if success else StepStatus.FAILED
                return StepResult(
                    step_id=step.id,
                    title=step.title,
                    step_type=step.step_type,
                    status=status,
                    message=(
                        "Basic verification completed."
                        if status == StepStatus.COMPLETED
                        else "Basic verification failed."
                    ),
                    phase=step.phase,
                    details=verification_result,
                )
            if verification.commands:
                command_results = verification_manager.run_commands(
                    cancellation_event=cancellation_event
                )
                success = all(result.success for result in command_results)
                return StepResult(
                    step_id=step.id,
                    title=step.title,
                    step_type=step.step_type,
                    status=StepStatus.COMPLETED if success else StepStatus.FAILED,
                    message=(
                        "Configured verification commands completed successfully."
                        if success
                        else "Configured verification commands failed."
                    ),
                    phase=step.phase,
                    details={
                        "verification_commands": [
                            result.to_dict() for result in command_results
                        ]
                    },
                )
            return StepResult(
                step_id=step.id,
                title=step.title,
                step_type=step.step_type,
                status=StepStatus.SKIPPED,
                message="Basic verification has no built-in check for this action and no explicit verification commands were provided.",
                phase=step.phase,
            )

        command_results = verification_manager.run_commands(
            cancellation_event=cancellation_event
        )
        success = all(result.success for result in command_results)
        return StepResult(
            step_id=step.id,
            title=step.title,
            step_type=step.step_type,
            status=StepStatus.COMPLETED if success else StepStatus.FAILED,
            message=(
                "Verification commands completed successfully."
                if success
                else "One or more verification commands failed."
            ),
            phase=step.phase,
            details={
                "verification_commands": [
                    result.to_dict() for result in command_results
                ]
            },
        )

    @staticmethod
    def _completed_step(step, message: str, details: dict | None = None) -> StepResult:
        return StepResult(
            step_id=step.id,
            title=step.title,
            step_type=step.step_type,
            status=StepStatus.COMPLETED,
            message=message,
            phase=step.phase,
            details=details or {},
        )

    @staticmethod
    def _unsupported_step_result(step) -> StepResult:
        status = (
            StepStatus.COMPLETED
            if step.step_type == StepType.DISCOVERY
            else StepStatus.SKIPPED
        )
        message = (
            "Discovery completed for an unsupported execution type."
            if step.step_type == StepType.DISCOVERY
            else "Execution was skipped because this action is not implemented in this phase."
        )
        return StepResult(
            step_id=step.id,
            title=step.title,
            step_type=step.step_type,
            status=status,
            message=message,
            phase=step.phase,
            details={},
        )

    def _handle_step_failure(
        self,
        plan: Plan,
        step_results: list[StepResult],
        error: Exception,
    ) -> tuple[ExecutionResult, list[StepResult]]:
        completed_ids = {step.step_id for step in step_results}
        failure_step = next(
            (step for step in plan.steps if step.id not in completed_ids), None
        )
        if failure_step is not None:
            step_results.append(
                StepResult(
                    step_id=failure_step.id,
                    title=failure_step.title,
                    step_type=failure_step.step_type,
                    status=StepStatus.FAILED,
                    message=str(error),
                    phase=failure_step.phase,
                    details={},
                )
            )
        remaining_ids = {step.step_id for step in step_results}
        remaining_steps = [step for step in plan.steps if step.id not in remaining_ids]
        step_results.extend(self._skip_remaining_steps(remaining_steps))
        return (
            ExecutionResult(success=False, message=str(error), details={}),
            step_results,
        )

    @staticmethod
    def _skip_remaining_steps(steps) -> list[StepResult]:
        return [
            StepResult(
                step_id=step.id,
                title=step.title,
                step_type=step.step_type,
                status=StepStatus.SKIPPED,
                message="Skipped because a prior step failed or execution stopped.",
                phase=step.phase,
                details={},
            )
            for step in steps
        ]

    def _approve_phase_if_needed(
        self,
        approval_mode: ApprovalMode,
        phase: str,
        approved_phases: set[str],
        phase_policies,
    ) -> bool:
        policy_map = {policy.phase: policy for policy in phase_policies}
        policy = policy_map.get(phase)
        if policy is not None and policy.auto_run_allowed:
            policy.auto_ran = True
            approved_phases.add(phase)
            print(
                f"\nAuto-running read-only phase '{phase}' because it is system-classified as {policy.classification.value}."
            )
            return True
        if approval_mode != ApprovalMode.PHASED or phase in approved_phases:
            approved_phases.add(phase)
            return True
        approved = self._request_phase_approval(phase)
        if approved:
            approved_phases.add(phase)
        return approved

    def _declined_phase_result(
        self,
        plan: Plan,
        step_results: list[StepResult],
        phase: str,
    ) -> tuple[ExecutionResult, list[StepResult]]:
        completed_ids = {step.step_id for step in step_results}
        remaining_steps = [step for step in plan.steps if step.id not in completed_ids]
        for step in remaining_steps:
            step_results.append(
                StepResult(
                    step_id=step.id,
                    title=step.title,
                    step_type=step.step_type,
                    status=StepStatus.SKIPPED,
                    message=(
                        f"Skipped because phase '{phase}' was not approved."
                        if step.phase == phase
                        else f"Skipped because execution stopped before phase '{step.phase}'."
                    ),
                    phase=step.phase,
                    details={},
                )
            )
        return (
            ExecutionResult(
                success=False,
                message=f"Execution stopped because phase '{phase}' was not approved.",
                details={"stopped_phase": phase},
            ),
            step_results,
        )

    @staticmethod
    def _validate_verification_configuration(
        mode: Mode, verification: VerificationConfig
    ) -> None:
        if (
            verification.profile == VerificationProfile.COMMANDS_ONLY
            and mode != Mode.IMPLEMENT
        ):
            raise RuntimeError(
                "The 'commands_only' verification profile is only allowed in IMPLEMENT mode."
            )
        if (
            verification.profile == VerificationProfile.COMMANDS_ONLY
            and not verification.commands
        ):
            raise RuntimeError(
                "The 'commands_only' verification profile requires at least one --verify-command."
            )

    def _interpret_task(self, task: str) -> TaskInterpretation:
        normalized = task.strip()
        lower = normalized.lower()

        create_match = re.search(
            r"create a file named\s+(?P<path>[^\s]+)\s+with\s+(?P<content>.+)",
            normalized,
            re.IGNORECASE,
        )
        if create_match:
            return TaskInterpretation(
                raw_task=task,
                summary=f"Create file '{create_match.group('path')}' with provided content.",
                action=TaskAction.CREATE_FILE,
                target_path=create_match.group("path"),
                content=create_match.group("content"),
            )

        modify_match = re.search(
            r"(?:modify|replace contents of|overwrite|update)\s+(?:file\s+)?(?P<path>[^\s]+)\s+(?:to contain:?|with new content:?|with:?|to:?)(?P<content>.+)",
            normalized,
            re.IGNORECASE,
        )
        if modify_match:
            return TaskInterpretation(
                raw_task=task,
                summary=f"Modify file '{modify_match.group('path')}' with provided content.",
                action=TaskAction.MODIFY_FILE,
                target_path=modify_match.group("path"),
                content=modify_match.group("content").strip(),
            )

        command_match = re.search(
            r"(?:run|execute) (?:the )?command\s+(?P<command>.+)",
            normalized,
            re.IGNORECASE,
        )
        if command_match:
            return TaskInterpretation(
                raw_task=task,
                summary="Run a shell command in the workspace.",
                action=TaskAction.RUN_COMMAND,
                command=command_match.group("command"),
            )

        restore_match = re.search(
            r"restore(?: snapshot)?(?:\s+(?P<snapshot_id>[A-Za-z0-9_\-]+)|\s+latest)?",
            lower,
        )
        if restore_match and lower.startswith("restore"):
            snapshot_id = restore_match.group("snapshot_id")
            return TaskInterpretation(
                raw_task=task,
                summary="Restore the workspace from a snapshot.",
                action=TaskAction.RESTORE_SNAPSHOT,
                snapshot_id=snapshot_id,
            )

        file_match = re.search(
            r"(?:explain|analyze|read|inspect)\s+(?:this file\s+)?(?P<path>[^\s]+)",
            normalized,
            re.IGNORECASE,
        )
        if file_match:
            return TaskInterpretation(
                raw_task=task,
                summary=f"Analyze file '{file_match.group('path')}'.",
                action=TaskAction.ANALYZE,
                target_path=file_match.group("path"),
            )

        return TaskInterpretation(
            raw_task=task,
            summary="Complex request requiring decomposition.",
            action=TaskAction.COMPLEX,
        )

    def _request_plan_approval(self, plan: Plan) -> bool:
        print("\nGenerated plan:")
        for step in plan.steps:
            dependencies = ", ".join(step.dependencies) if step.dependencies else "none"
            print(f"- {step.id}: {step.title}")
            print(f"  type: {step.step_type.value}")
            print(f"  phase: {step.phase}")
            print(f"  description: {step.description}")
            print(f"  dependencies: {dependencies}")
            print(f"  scope hint: {step.scope_hint}")
            print(f"  risk hint: {step.risk_hint}")
            print(f"  approval hint: {step.approval_hint}")
            print(f"  expected outcome: {step.expected_outcome}")
        response = input("\nApprove plan and continue? (y/n): ").strip().lower()
        return response == "y"

    @staticmethod
    def _summarize_file(relative_path: str, content: str) -> str:
        preview = content[:500]
        return (
            f"Read '{relative_path}'. The file is {len(content)} character(s) long. "
            f"Preview:\n{preview}"
        )

    @staticmethod
    def _conversation_repo_note(repo_index_summary: RepoIndexSummary) -> str:
        top_dirs = (
            ", ".join(repo_index_summary.top_level_directories[:5])
            or "no top-level directories"
        )
        return (
            "Repo index is available for this run. "
            f"It saw {repo_index_summary.total_files} file(s) and top-level directories: {top_dirs}."
        )

    def run(
        self,
        mode: Mode,
        task: str,
        scope_paths: list[str] | None = None,
        protected_paths: list[str] | None = None,
        verification_config: VerificationConfig | None = None,
        project_memory: ProjectMemoryContext | None = None,
        build_repo_index: bool = False,
        command_policy: CommandPolicyMode = CommandPolicyMode.PERMISSIVE,
        preview_changes: bool = False,
        auto_repair: bool = False,
        auto_repair_attempts: int = 1,
        approval_mode: ApprovalMode = ApprovalMode.NORMAL,
    ) -> TaskResult:
        auto_repair_attempts = max(1, auto_repair_attempts)
        permissions = PermissionManager.for_mode(mode)
        verification = verification_config or VerificationConfig()
        artifact_manager = ArtifactManager(self.project_root)
        artifact_references: list[ArtifactReference] = [
            artifact_manager.run_reference()
        ]
        step_results: list[StepResult] = []
        phase_results: list[PhaseResult] = []
        snapshot_reference = None
        interpretation = self._interpret_task(task)
        phase_policies = []
        initial_execution_result: ExecutionResult | None = None
        failure_classification: FailureClassification | None = None
        repair_summary: RepairSummary | None = None
        llm_summary: LLMStructuredResult | None = None
        llm_review_summary: LLMStructuredResult | None = None

        try:
            scope_manager = ScopeManager(
                self.project_root, scope_paths, protected_paths
            )
        except ValueError as error:
            scope_manager = ScopeManager(self.project_root)
            workspace = WorkspaceManager(self.project_root, permissions, scope_manager)
            terminal = TerminalRunner(
                self.project_root, permissions, command_policy, artifact_manager
            )
            final_execution_result = ExecutionResult(
                success=False, message=str(error), details={}
            )
            result = self._build_result(
                mode=mode,
                permissions=permissions,
                approval_mode=approval_mode,
                command_policy=command_policy,
                scope_manager=scope_manager,
                verification=verification,
                interpretation=interpretation,
                repo_index_summary=None,
                change_preview=None,
                llm_summary=None,
                llm_review_summary=None,
                plan=None,
                phase_policies=[],
                context_package=None,
                workspace=workspace,
                terminal=terminal,
                artifact_references=artifact_references,
                phase_results=[],
                step_results=[],
                initial_execution_result=final_execution_result,
                failure_classification=None,
                repair_summary=None,
                final_execution_result=final_execution_result,
                snapshot_reference=None,
            )
            self._attach_result_artifact(artifact_manager, artifact_references, result)
            return result

        workspace = WorkspaceManager(self.project_root, permissions, scope_manager)
        terminal = TerminalRunner(
            self.project_root, permissions, command_policy, artifact_manager
        )
        preview_manager = PreviewManager(workspace, scope_manager, artifact_manager)
        snapshots = SnapshotManager(self.project_root)
        verification_manager = VerificationManager(verification, workspace, terminal)
        change_preview = None
        preview_snapshot_reference = None
        if preview_changes and mode == Mode.IMPLEMENT:
            preview_snapshot_reference = snapshots.create_snapshot()
            change_preview = preview_manager.build_preview(
                interpretation,
                snapshot_reference=preview_snapshot_reference,
                plan_steps=[],
                persist=False,
            )
        repo_index_summary = self._build_repo_index(
            build_repo_index,
            artifact_manager,
            artifact_references,
            workspace,
            scope_manager,
        )

        plan, llm_summary, llm_review_summary = self.build_plan_with_local_llm(
            mode=mode,
            interpretation=interpretation,
            scope_manager=scope_manager,
            verification=verification,
            repo_index_summary=repo_index_summary,
            project_memory=project_memory,
            artifact_manager=artifact_manager,
            artifact_references=artifact_references,
        )
        if plan is not None:
            phase_policies = classify_plan_phases(plan, interpretation)
            artifact_references.append(
                artifact_manager.save_json(
                    artifact_manager.build_filename("plan"),
                    plan.to_dict(),
                )
            )

        if change_preview is not None and plan is not None:
            change_preview.relevant_steps = [step.title for step in plan.steps]
            change_preview = preview_manager.persist_preview(change_preview)
            if change_preview.artifact_reference is not None:
                artifact_references.append(change_preview.artifact_reference)
        if change_preview is not None:
            self._print_change_preview(change_preview)

        context_package = self.context_builder.build(
            task=task,
            mode=mode,
            approval_mode=approval_mode,
            effective_scope=scope_manager.describe_effective_scope(),
            protected_paths=scope_manager.protected_path_labels(),
            verification=verification,
            command_policy=command_policy,
            project_memory=project_memory,
            repo_index_summary=repo_index_summary,
            plan=plan,
            phase_policies=phase_policies,
            change_preview=change_preview,
            llm_summary=llm_summary,
            llm_review_summary=llm_review_summary,
            phase_results=phase_results,
            verification_outcomes=[],
            artifact_references=artifact_references,
        )

        try:
            self._validate_verification_configuration(mode, verification)
            if mode == Mode.CONVERSATION:
                final_execution_result = self._handle_conversation(
                    workspace, interpretation, repo_index_summary
                )
            elif mode == Mode.PLAN:
                final_execution_result = ExecutionResult(
                    success=True,
                    message="Plan generated. No execution was performed.",
                    details={},
                )
            else:
                if plan is None:
                    raise RuntimeError(
                        "IMPLEMENT mode requires a plan before execution."
                    )
                if not self._request_plan_approval(plan):
                    final_execution_result = ExecutionResult(
                        success=False,
                        message="Plan was not approved. No execution was performed.",
                        details={},
                    )
                else:
                    snapshot_reference = snapshots.create_snapshot()
                    initial_execution_result, step_results = self._execute_plan(
                        plan=plan,
                        phase_policies=phase_policies,
                        scope_manager=scope_manager,
                        workspace=workspace,
                        terminal=terminal,
                        snapshots=snapshots,
                        verification_manager=verification_manager,
                        interpretation=interpretation,
                        verification=verification,
                        approval_mode=approval_mode,
                    )
                    phase_results = self._build_phase_results(step_results)
                    for policy in phase_policies:
                        if (
                            policy.phase
                            in {
                                step.phase
                                for step in step_results
                                if step.status == StepStatus.COMPLETED
                            }
                            and not policy.approval_required
                        ):
                            policy.auto_ran = True
                    phase_results = self._build_phase_results_with_policy(
                        step_results, phase_policies
                    )
                    final_execution_result, failure_classification, repair_summary = (
                        self._handle_post_execution_failure(
                            artifact_manager=artifact_manager,
                            artifact_references=artifact_references,
                            auto_repair=auto_repair,
                            auto_repair_attempts=auto_repair_attempts,
                            initial_execution_result=initial_execution_result,
                            step_results=step_results,
                            workspace=workspace,
                            terminal=terminal,
                            verification_manager=verification_manager,
                            interpretation=interpretation,
                            verification=verification,
                            repo_index_summary=repo_index_summary,
                            scope_manager=scope_manager,
                        )
                    )
        except (
            PermissionDeniedError,
            FileNotFoundError,
            ValueError,
            RuntimeError,
            ScopeViolationError,
            CommandPolicyError,
        ) as error:
            final_execution_result = ExecutionResult(
                success=False, message=str(error), details={}
            )
            initial_execution_result = (
                initial_execution_result or final_execution_result
            )
            if mode == Mode.IMPLEMENT:
                failure_classification = self.failure_classifier.classify(
                    final_execution_result.message,
                    step_results,
                    workspace.blocked_actions,
                    terminal.commands_run,
                )
                failure_classification = self._persist_failure_report(
                    artifact_manager,
                    artifact_references,
                    failure_classification,
                    final_execution_result,
                    step_results,
                )
                repair_summary = self.repair_manager.build_repair_summary(
                    auto_repair_enabled=auto_repair,
                    failure=failure_classification,
                    interpretation=interpretation,
                    verification=verification,
                    repo_index_summary=repo_index_summary,
                    scope_manager=scope_manager,
                    repair_attempt_limit=auto_repair_attempts,
                )
                repair_summary = self._persist_repair_plan(
                    artifact_manager,
                    artifact_references,
                    repair_summary,
                )
            phase_results = self._build_phase_results_with_policy(
                step_results, phase_policies
            )

        verification_outcomes = [
            step.details
            for step in step_results
            if step.step_type == StepType.VERIFICATION and step.details
        ]
        context_package = self.context_builder.build(
            task=task,
            mode=mode,
            approval_mode=approval_mode,
            effective_scope=scope_manager.describe_effective_scope(),
            protected_paths=scope_manager.protected_path_labels(),
            verification=verification,
            command_policy=command_policy,
            project_memory=project_memory,
            repo_index_summary=repo_index_summary,
            plan=plan,
            phase_policies=phase_policies,
            change_preview=change_preview,
            llm_summary=llm_summary,
            llm_review_summary=llm_review_summary,
            phase_results=phase_results,
            verification_outcomes=verification_outcomes,
            artifact_references=artifact_references,
        )

        result = self._build_result(
            mode=mode,
            permissions=permissions,
            approval_mode=approval_mode,
            command_policy=command_policy,
            scope_manager=scope_manager,
            verification=verification,
            interpretation=interpretation,
            repo_index_summary=repo_index_summary,
            change_preview=change_preview,
            llm_summary=llm_summary,
            llm_review_summary=llm_review_summary,
            plan=plan,
            phase_policies=phase_policies,
            context_package=context_package,
            workspace=workspace,
            terminal=terminal,
            artifact_references=artifact_references,
            phase_results=phase_results,
            step_results=step_results,
            initial_execution_result=initial_execution_result,
            failure_classification=failure_classification,
            repair_summary=repair_summary,
            final_execution_result=final_execution_result,
            snapshot_reference=snapshot_reference,
        )
        self._attach_result_artifact(artifact_manager, artifact_references, result)
        return result


def format_result(result: TaskResult) -> str:
    return json.dumps(result.to_dict(), indent=2)
