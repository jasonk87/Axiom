from __future__ import annotations

from models import (
    CommandResult,
    ExecutionResult,
    FailureCategory,
    FailureClassification,
    Plan,
    PlanStep,
    RepoIndexSummary,
    RepairSummary,
    StepResult,
    StepStatus,
    StepType,
    TaskAction,
    TaskInterpretation,
    VerificationConfig,
)
from scope_manager import ScopeManager
from terminal_runner import TerminalRunner
from verification_manager import VerificationManager
from workspace_manager import WorkspaceManager


class RepairManager:
    def build_repair_summary(
        self,
        auto_repair_enabled: bool,
        failure: FailureClassification,
        interpretation: TaskInterpretation,
        verification: VerificationConfig,
        repo_index_summary: RepoIndexSummary | None,
        scope_manager: ScopeManager,
        repair_attempt_limit: int = 1,
    ) -> RepairSummary:
        repair_plan, eligible, reason = self._build_repair_plan(
            failure,
            interpretation,
            verification,
            repo_index_summary,
            scope_manager,
        )
        return RepairSummary(
            attempted=False,
            auto_repair_enabled=auto_repair_enabled,
            eligible=eligible,
            reason=reason,
            repair_attempt_limit=max(1, repair_attempt_limit),
            repair_plan=repair_plan,
        )

    def attempt_repair(
        self,
        repair_summary: RepairSummary,
        failure: FailureClassification,
        interpretation: TaskInterpretation,
        verification: VerificationConfig,
        workspace: WorkspaceManager,
        terminal: TerminalRunner,
        verification_manager: VerificationManager,
        max_attempts: int = 1,
    ) -> RepairSummary:
        if not repair_summary.eligible or repair_summary.repair_plan is None:
            return repair_summary

        step_results: list[StepResult] = []
        max_attempts = max(1, min(max_attempts, repair_summary.repair_attempt_limit))
        try:
            if (
                failure.category == FailureCategory.COMMAND_EXECUTION_FAILURE
                and interpretation.command is not None
            ):
                repair_summary.repair_strategies_tried.append("retry_original_command")
                max_attempts = max(1, max_attempts)
                step_results.append(
                    self._completed(
                        repair_summary.repair_plan.steps[0],
                        "Reviewed failed command output.",
                    )
                )
                latest_retry_result = None
                verification_step = None
                for attempt_index in range(1, max_attempts + 1):
                    retry_result = terminal.run(interpretation.command)
                    latest_retry_result = retry_result
                    retry_step_status = (
                        StepStatus.COMPLETED
                        if retry_result.success
                        else StepStatus.FAILED
                    )
                    retry_details = retry_result.to_dict()
                    retry_details["repair_attempt"] = attempt_index
                    retry_details["repair_attempt_limit"] = max_attempts
                    step_results.append(
                        StepResult(
                            step_id=repair_summary.repair_plan.steps[1].id,
                            title=repair_summary.repair_plan.steps[1].title,
                            step_type=repair_summary.repair_plan.steps[1].step_type,
                            status=retry_step_status,
                            message=(
                                f"Repair command attempt {attempt_index}/{max_attempts}: "
                                f"{retry_result.summary}"
                            ),
                            phase=repair_summary.repair_plan.steps[1].phase,
                            details=retry_details,
                        )
                    )
                    repair_summary.repair_attempts_used = attempt_index
                    if not retry_result.success:
                        continue

                    verification_step = self._rerun_verification_for_command(
                        repair_summary.repair_plan.steps[2],
                        verification,
                        verification_manager,
                    )
                    verification_step.details["repair_attempt"] = attempt_index
                    verification_step.details["repair_attempt_limit"] = max_attempts
                    step_results.append(verification_step)
                    if verification_step.status != StepStatus.FAILED:
                        break

                repair_summary.attempted = True
                repair_summary.repair_step_results = step_results
                if (
                    latest_retry_result is None
                    or not latest_retry_result.success
                    or (
                        verification_step is not None
                        and verification_step.status == StepStatus.FAILED
                    )
                ):
                    step_results.append(
                        self._failed(
                            repair_summary.repair_plan.steps[2],
                            "All bounded repair attempts were exhausted.",
                        )
                    )
                    repair_summary.repair_execution_result = ExecutionResult(
                        success=False,
                        message=(
                            "Repair retries failed after "
                            f"{repair_summary.repair_attempts_used} attempt(s)."
                        ),
                        details=self._attempt_metadata(
                            latest_retry_result,
                            repair_summary.repair_attempts_used,
                            max_attempts,
                        ),
                    )
                    return repair_summary

                repair_summary.repair_execution_result = ExecutionResult(
                    success=True,
                    message=(
                        "Repair retry succeeded on attempt "
                        f"{repair_summary.repair_attempts_used}."
                    ),
                    details=(
                        verification_step.details
                        if verification_step is not None and verification_step.details
                        else latest_retry_result.to_dict()
                    ),
                )
                return repair_summary

            if (
                failure.category == FailureCategory.VERIFICATION_FAILURE
                and interpretation.action
                in {TaskAction.CREATE_FILE, TaskAction.MODIFY_FILE}
                and interpretation.target_path is not None
                and interpretation.content is not None
            ):
                repair_summary.repair_strategies_tried.append(
                    "rewrite_and_reverify_target"
                )
                step_results.append(
                    self._completed(
                        repair_summary.repair_plan.steps[0],
                        "Reviewed verification failure for the written file.",
                    )
                )
                last_verification_result: dict | None = None
                succeeded = False
                for attempt_index in range(1, max_attempts + 1):
                    workspace.write_text(
                        interpretation.target_path, interpretation.content
                    )
                    step_results.append(
                        self._completed(
                            repair_summary.repair_plan.steps[1],
                            (
                                "Reapplied requested file content under the original "
                                f"scope and protections (attempt {attempt_index}/{max_attempts})."
                            ),
                            {
                                "target_path": interpretation.target_path,
                                "repair_attempt": attempt_index,
                                "repair_attempt_limit": max_attempts,
                            },
                        )
                    )
                    verification_result = verification_manager.verify_file_write(
                        interpretation.target_path,
                        interpretation.content,
                    )
                    verification_result["repair_attempt"] = attempt_index
                    verification_result["repair_attempt_limit"] = max_attempts
                    last_verification_result = verification_result
                    status = (
                        StepStatus.COMPLETED
                        if (
                            verification_result["exists"]
                            and verification_result["content_matches"]
                            and verification_result.get(
                                "configured_commands_passed", True
                            )
                        )
                        else StepStatus.FAILED
                    )
                    step_results.append(
                        StepResult(
                            step_id=repair_summary.repair_plan.steps[2].id,
                            title=repair_summary.repair_plan.steps[2].title,
                            step_type=repair_summary.repair_plan.steps[2].step_type,
                            status=status,
                            message=(
                                f"Post-repair verification passed on attempt {attempt_index}/{max_attempts}."
                                if status == StepStatus.COMPLETED
                                else f"Post-repair verification failed on attempt {attempt_index}/{max_attempts}."
                            ),
                            phase=repair_summary.repair_plan.steps[2].phase,
                            details=verification_result,
                        )
                    )
                    repair_summary.repair_attempts_used = attempt_index
                    if status == StepStatus.COMPLETED:
                        succeeded = True
                        break

                repair_summary.attempted = True
                repair_summary.repair_step_results = step_results
                repair_summary.repair_execution_result = ExecutionResult(
                    success=succeeded,
                    message=(
                        "Repair rewrite completed successfully."
                        if succeeded
                        else (
                            "Repair rewrite exhausted bounded attempts without passing verification."
                        )
                    ),
                    details=last_verification_result or {},
                )
                return repair_summary
        except Exception as error:
            step_results.append(
                self._failed(repair_summary.repair_plan.steps[-1], str(error))
            )
            repair_summary.attempted = True
            if repair_summary.repair_attempts_used == 0:
                repair_summary.repair_attempts_used = 1
            repair_summary.repair_step_results = step_results
            repair_summary.repair_execution_result = ExecutionResult(
                success=False,
                message=str(error),
                details={},
            )
            return repair_summary

        return repair_summary

    def _build_repair_plan(
        self,
        failure: FailureClassification,
        interpretation: TaskInterpretation,
        verification: VerificationConfig,
        repo_index_summary: RepoIndexSummary | None,
        scope_manager: ScopeManager,
    ) -> tuple[Plan | None, bool, str]:
        scope_hint = scope_manager.target_scope_hint(interpretation.target_path)
        repo_note = self._repo_note(repo_index_summary)

        if failure.category in {
            FailureCategory.SCOPE_VIOLATION,
            FailureCategory.PROTECTED_PATH_VIOLATION,
            FailureCategory.WRITE_POLICY_BLOCK,
            FailureCategory.COMMAND_POLICY_BLOCK,
        }:
            plan = Plan(
                steps=[
                    PlanStep(
                        id="repair-step-1",
                        step_type=StepType.DISCOVERY,
                        title="Review Policy Block",
                        description=f"Confirm why execution was blocked and keep the original safety boundary intact.{repo_note}",
                        dependencies=[],
                        scope_hint=scope_hint,
                        expected_outcome="The failure is understood without attempting a bypass.",
                        phase="repair_follow_up",
                        risk_hint="low",
                        approval_hint="included_in_run_approval",
                    ),
                    PlanStep(
                        id="repair-step-2",
                        step_type=StepType.VERIFICATION,
                        title="Report Required User Change",
                        description="Report that repair is not eligible because the failure came from an enforced policy boundary.",
                        dependencies=["repair-step-1"],
                        scope_hint=scope_hint,
                        expected_outcome="The user can choose a different scope, path, or command explicitly in a later run.",
                        phase="repair_follow_up",
                        risk_hint="low",
                        approval_hint="included_in_run_approval",
                    ),
                ]
            )
            return plan, False, "Repair is not eligible for policy or scope violations."

        if (
            failure.category == FailureCategory.COMMAND_EXECUTION_FAILURE
            and interpretation.command is not None
        ):
            plan = Plan(
                steps=[
                    PlanStep(
                        id="repair-step-1",
                        step_type=StepType.DISCOVERY,
                        title="Inspect Failed Command",
                        description=f"Review the failed command output to confirm the retry should remain narrow and unchanged.{repo_note}",
                        dependencies=[],
                        scope_hint=scope_hint,
                        expected_outcome="The failure context is understood before retrying.",
                        phase="repair_follow_up",
                        risk_hint="low",
                        approval_hint="included_in_run_approval",
                    ),
                    PlanStep(
                        id="repair-step-2",
                        step_type=StepType.EXECUTION,
                        title="Retry Command (Bounded)",
                        description=f"Retry the original command within the configured bounded attempt budget without changing scope, permissions, or command text: {interpretation.command}",
                        dependencies=["repair-step-1"],
                        scope_hint=scope_hint,
                        expected_outcome="A bounded retry series is attempted under the same policy.",
                        phase="repair_follow_up",
                        risk_hint="medium",
                        approval_hint="included_in_run_approval",
                    ),
                    PlanStep(
                        id="repair-step-3",
                        step_type=StepType.VERIFICATION,
                        title="Re-check Command Outcome",
                        description=self._verification_description(verification),
                        dependencies=["repair-step-2"],
                        scope_hint=scope_hint,
                        expected_outcome="The retry outcome is verified and reported clearly.",
                        phase="repair_follow_up",
                        risk_hint="low",
                        approval_hint="included_in_run_approval",
                    ),
                ]
            )
            return (
                plan,
                True,
                "Bounded retry is eligible for command execution failure.",
            )

        if (
            failure.category == FailureCategory.VERIFICATION_FAILURE
            and interpretation.action
            in {TaskAction.CREATE_FILE, TaskAction.MODIFY_FILE}
            and interpretation.target_path is not None
        ):
            plan = Plan(
                steps=[
                    PlanStep(
                        id="repair-step-1",
                        step_type=StepType.DISCOVERY,
                        title="Inspect Verification Mismatch",
                        description=f"Inspect why verification failed for '{interpretation.target_path}' without changing scope or protections.{repo_note}",
                        dependencies=[],
                        scope_hint=scope_hint,
                        expected_outcome="The mismatch is narrowed to the originally requested file write.",
                        phase="repair_follow_up",
                        risk_hint="low",
                        approval_hint="included_in_run_approval",
                    ),
                    PlanStep(
                        id="repair-step-2",
                        step_type=StepType.EXECUTION,
                        title="Reapply Requested Write",
                        description=f"Reapply the originally requested content to '{interpretation.target_path}' under the same write rules.",
                        dependencies=["repair-step-1"],
                        scope_hint=scope_hint,
                        expected_outcome="The original requested content is written again without expanding permissions.",
                        phase="repair_follow_up",
                        risk_hint="medium",
                        approval_hint="included_in_run_approval",
                    ),
                    PlanStep(
                        id="repair-step-3",
                        step_type=StepType.VERIFICATION,
                        title="Re-run File Verification",
                        description="Re-run direct file verification after the reapply step.",
                        dependencies=["repair-step-2"],
                        scope_hint=scope_hint,
                        expected_outcome="Post-repair verification confirms whether the requested file state was achieved.",
                        phase="repair_follow_up",
                        risk_hint="low",
                        approval_hint="included_in_run_approval",
                    ),
                ]
            )
            return (
                plan,
                True,
                "A single narrow rewrite is eligible for direct file verification failure.",
            )

        plan = Plan(
            steps=[
                PlanStep(
                    id="repair-step-1",
                    step_type=StepType.DISCOVERY,
                    title="Inspect Failure Context",
                    description=f"Review the failure details and note which unknowns remain before any future follow-up.{repo_note}",
                    dependencies=[],
                    scope_hint=scope_hint,
                    expected_outcome="The failure is summarized clearly without speculative repair behavior.",
                    phase="repair_follow_up",
                    risk_hint="low",
                    approval_hint="included_in_run_approval",
                ),
                PlanStep(
                    id="repair-step-2",
                    step_type=StepType.VERIFICATION,
                    title="Stop After Reporting",
                    description="Stop after reporting because this phase does not support a narrow automatic repair for this failure type.",
                    dependencies=["repair-step-1"],
                    scope_hint=scope_hint,
                    expected_outcome="The run ends predictably with a clear failure report.",
                    phase="repair_follow_up",
                    risk_hint="low",
                    approval_hint="included_in_run_approval",
                ),
            ]
        )
        return plan, False, "No safe automatic repair is defined for this failure type."

    @staticmethod
    def _verification_description(verification: VerificationConfig) -> str:
        if verification.profile.value == "commands_only":
            return "Re-run the configured verification commands after the retry."
        if verification.profile.value == "basic":
            return "Re-run the built-in verification check after the retry."
        return "Record the retry result even though no additional verification profile is enabled."

    @staticmethod
    def _repo_note(repo_index_summary: RepoIndexSummary | None) -> str:
        if repo_index_summary is None or not repo_index_summary.generated:
            return ""
        if repo_index_summary.likely_test_files:
            return (
                " Repo index indicates likely test coverage in "
                + ", ".join(repo_index_summary.likely_test_files[:2])
                + "."
            )
        return " Repo index is available for this repair context."

    @staticmethod
    def _completed(
        step: PlanStep, message: str, details: dict | None = None
    ) -> StepResult:
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
    def _failed(step: PlanStep, message: str) -> StepResult:
        return StepResult(
            step_id=step.id,
            title=step.title,
            step_type=step.step_type,
            status=StepStatus.FAILED,
            message=message,
            phase=step.phase,
            details={},
        )

    def _rerun_verification_for_command(
        self,
        step: PlanStep,
        verification: VerificationConfig,
        verification_manager: VerificationManager,
    ) -> StepResult:
        if verification.profile.value != "commands_only":
            return StepResult(
                step_id=step.id,
                title=step.title,
                step_type=step.step_type,
                status=StepStatus.SKIPPED,
                message="No command-based verification was configured for the retry.",
                phase=step.phase,
                details={},
            )

        command_results = verification_manager.run_commands()
        success = all(result.success for result in command_results)
        return StepResult(
            step_id=step.id,
            title=step.title,
            step_type=step.step_type,
            status=StepStatus.COMPLETED if success else StepStatus.FAILED,
            message=(
                "Post-repair verification commands passed."
                if success
                else "Post-repair verification commands failed."
            ),
            phase=step.phase,
            details={
                "verification_commands": [
                    result.to_dict() for result in command_results
                ]
            },
        )

    @staticmethod
    def _attempt_metadata(
        command_result: ExecutionResult | CommandResult | None,
        attempt: int,
        attempt_limit: int,
    ) -> dict:
        base = command_result.to_dict() if command_result is not None else {}
        base["repair_attempt"] = attempt
        base["repair_attempt_limit"] = attempt_limit
        return base
