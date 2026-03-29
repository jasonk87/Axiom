from __future__ import annotations

from models import (
    Mode,
    Plan,
    PlanStep,
    RepoIndexSummary,
    StepType,
    TaskAction,
    TaskInterpretation,
    VerificationConfig,
)
from scope_manager import ScopeManager


class Planner:
    def build_plan(
        self,
        mode: Mode,
        interpretation: TaskInterpretation,
        scope_manager: ScopeManager,
        verification_config: VerificationConfig,
        repo_index_summary: RepoIndexSummary | None = None,
    ) -> Plan | None:
        if mode == Mode.CONVERSATION:
            return None

        if interpretation.action == TaskAction.CREATE_FILE:
            return self._plan_for_create_file(
                interpretation,
                scope_manager,
                verification_config,
                repo_index_summary,
            )
        if interpretation.action == TaskAction.MODIFY_FILE:
            return self._plan_for_modify_file(
                interpretation,
                scope_manager,
                verification_config,
                repo_index_summary,
            )
        if interpretation.action == TaskAction.RUN_COMMAND:
            return self._plan_for_command(
                interpretation,
                scope_manager,
                verification_config,
                repo_index_summary,
            )
        if interpretation.action == TaskAction.RESTORE_SNAPSHOT:
            return self._plan_for_restore(
                interpretation,
                scope_manager,
                verification_config,
                repo_index_summary,
            )

        return self._generic_analysis_plan(scope_manager, repo_index_summary)

    def _plan_for_create_file(
        self,
        interpretation: TaskInterpretation,
        scope_manager: ScopeManager,
        verification_config: VerificationConfig,
        repo_index_summary: RepoIndexSummary | None,
    ) -> Plan:
        target = interpretation.target_path or "<unknown>"
        repo_note = self._repo_note_for_target(target, repo_index_summary)
        return Plan(
            steps=[
                PlanStep(
                    id="step-1",
                    step_type=StepType.DISCOVERY,
                    title="Inspect Target Path",
                    description=(
                        f"Determine whether '{target}' already exists, which directory it belongs to, and whether the requested file fits the active scope and protection rules."
                        f"{repo_note}"
                    ),
                    dependencies=[],
                    scope_hint=scope_manager.target_scope_hint(target),
                    expected_outcome="The intended file change is understood before any write begins.",
                    phase="understand",
                    risk_hint="low",
                    approval_hint="review_before_modification_phase",
                ),
                PlanStep(
                    id="step-2",
                    step_type=StepType.EXECUTION,
                    title="Apply Requested File Change",
                    description=f"Create or overwrite '{target}' with the requested content if the path remains writable under current rules.",
                    dependencies=["step-1"],
                    scope_hint=scope_manager.target_scope_hint(target),
                    expected_outcome=f"The file '{target}' is written only within the approved scope.",
                    phase="modify",
                    risk_hint="medium",
                    approval_hint="high_value_if_phased_approval",
                ),
                PlanStep(
                    id="step-3",
                    step_type=StepType.VERIFICATION,
                    title="Verify File Outcome",
                    description=self._verification_description(
                        verification_config, TaskAction.CREATE_FILE
                    ),
                    dependencies=["step-2"],
                    scope_hint=scope_manager.target_scope_hint(target),
                    expected_outcome=self._verification_outcome(
                        verification_config, TaskAction.CREATE_FILE
                    ),
                    phase="verify",
                    risk_hint="low",
                    approval_hint="included_in_current_phase",
                ),
            ]
        )

    def _plan_for_command(
        self,
        interpretation: TaskInterpretation,
        scope_manager: ScopeManager,
        verification_config: VerificationConfig,
        repo_index_summary: RepoIndexSummary | None,
    ) -> Plan:
        command = interpretation.command or "<unknown>"
        repo_context = self._repo_context_phrase(repo_index_summary)
        return Plan(
            steps=[
                PlanStep(
                    id="step-1",
                    step_type=StepType.DISCOVERY,
                    title="Confirm Command Context",
                    description=(
                        "Confirm the command text, likely relevant files or directories, and the fact that shell side effects are not fully constrained by the workspace file manager."
                        f"{repo_context}"
                    ),
                    dependencies=[],
                    scope_hint=scope_manager.target_scope_hint(None),
                    expected_outcome="The command boundary and likely impact area are explicit before execution.",
                    phase="understand",
                    risk_hint="medium",
                    approval_hint="review_before_command_phase",
                ),
                PlanStep(
                    id="step-2",
                    step_type=StepType.EXECUTION,
                    title="Run Command",
                    description=f"Execute the shell command: {command}",
                    dependencies=["step-1"],
                    scope_hint=scope_manager.target_scope_hint(None),
                    expected_outcome="The command completes with structured stdout, stderr, and exit code capture.",
                    phase="modify",
                    risk_hint="medium",
                    approval_hint="high_value_if_phased_approval",
                ),
                PlanStep(
                    id="step-3",
                    step_type=StepType.VERIFICATION,
                    title="Verify Command Outcome",
                    description=self._verification_description(
                        verification_config, TaskAction.RUN_COMMAND
                    ),
                    dependencies=["step-2"],
                    scope_hint=scope_manager.target_scope_hint(None),
                    expected_outcome=self._verification_outcome(
                        verification_config, TaskAction.RUN_COMMAND
                    ),
                    phase="verify",
                    risk_hint="low",
                    approval_hint="included_in_current_phase",
                ),
            ]
        )

    def _plan_for_modify_file(
        self,
        interpretation: TaskInterpretation,
        scope_manager: ScopeManager,
        verification_config: VerificationConfig,
        repo_index_summary: RepoIndexSummary | None,
    ) -> Plan:
        target = interpretation.target_path or "<unknown>"
        repo_note = self._repo_note_for_target(target, repo_index_summary)
        return Plan(
            steps=[
                PlanStep(
                    id="step-1",
                    step_type=StepType.DISCOVERY,
                    title="Inspect Existing File State",
                    description=(
                        f"Determine whether '{target}' already exists, inspect its current state if readable, and confirm the overwrite remains inside active scope and protection rules."
                        f"{repo_note}"
                    ),
                    dependencies=[],
                    scope_hint=scope_manager.target_scope_hint(target),
                    expected_outcome="The overwrite target is understood before any content is replaced.",
                    phase="understand",
                    risk_hint="medium",
                    approval_hint="review_before_modification_phase",
                ),
                PlanStep(
                    id="step-2",
                    step_type=StepType.EXECUTION,
                    title="Overwrite File Content",
                    description=f"Replace the full contents of '{target}' with the requested content if the path remains writable under current rules.",
                    dependencies=["step-1"],
                    scope_hint=scope_manager.target_scope_hint(target),
                    expected_outcome=f"The file '{target}' contains only the newly requested content within the approved scope.",
                    phase="modify",
                    risk_hint="medium",
                    approval_hint="high_value_if_phased_approval",
                ),
                PlanStep(
                    id="step-3",
                    step_type=StepType.VERIFICATION,
                    title="Verify Overwrite Outcome",
                    description=self._verification_description(
                        verification_config, TaskAction.MODIFY_FILE
                    ),
                    dependencies=["step-2"],
                    scope_hint=scope_manager.target_scope_hint(target),
                    expected_outcome=self._verification_outcome(
                        verification_config, TaskAction.MODIFY_FILE
                    ),
                    phase="verify",
                    risk_hint="low",
                    approval_hint="included_in_current_phase",
                ),
            ]
        )

    def _plan_for_restore(
        self,
        interpretation: TaskInterpretation,
        scope_manager: ScopeManager,
        verification_config: VerificationConfig,
        repo_index_summary: RepoIndexSummary | None,
    ) -> Plan:
        requested = interpretation.snapshot_id or "latest snapshot"
        repo_context = self._repo_context_phrase(repo_index_summary)
        return Plan(
            steps=[
                PlanStep(
                    id="step-1",
                    step_type=StepType.DISCOVERY,
                    title="Identify Snapshot Source",
                    description=(
                        f"Determine which snapshot should be restored ({requested}) and whether the rollback target is compatible with the current run controls."
                        f"{repo_context}"
                    ),
                    dependencies=[],
                    scope_hint="whole project",
                    expected_outcome="The restore source is identified honestly before rollback begins.",
                    phase="understand",
                    risk_hint="medium",
                    approval_hint="review_before_modification_phase",
                ),
                PlanStep(
                    id="step-2",
                    step_type=StepType.EXECUTION,
                    title="Restore Workspace Snapshot",
                    description="Restore the workspace from the selected snapshot.",
                    dependencies=["step-1"],
                    scope_hint="whole project",
                    expected_outcome="Workspace files are reverted to the chosen snapshot state.",
                    phase="modify",
                    risk_hint="high",
                    approval_hint="high_value_if_phased_approval",
                ),
                PlanStep(
                    id="step-3",
                    step_type=StepType.VERIFICATION,
                    title="Verify Restore Outcome",
                    description=self._verification_description(
                        verification_config, TaskAction.RESTORE_SNAPSHOT
                    ),
                    dependencies=["step-2"],
                    scope_hint="whole project",
                    expected_outcome=self._verification_outcome(
                        verification_config, TaskAction.RESTORE_SNAPSHOT
                    ),
                    phase="verify",
                    risk_hint="medium",
                    approval_hint="included_in_current_phase",
                ),
            ]
        )

    def _generic_analysis_plan(
        self,
        scope_manager: ScopeManager,
        repo_index_summary: RepoIndexSummary | None,
    ) -> Plan:
        repo_context = self._generic_repo_discovery(repo_index_summary)
        return Plan(
            steps=[
                PlanStep(
                    id="step-1",
                    step_type=StepType.DISCOVERY,
                    title="Identify Relevant Files",
                    description=(
                        "Determine where the relevant definitions, dependencies, or affected files are located without assuming repo knowledge."
                        f"{repo_context}"
                    ),
                    dependencies=[],
                    scope_hint=scope_manager.target_scope_hint(None),
                    expected_outcome="The unknown parts of the task are reduced into explicit discovery findings.",
                    phase="understand",
                    risk_hint="low",
                    approval_hint="included_in_run_approval",
                ),
                PlanStep(
                    id="step-2",
                    step_type=StepType.VERIFICATION,
                    title="Summarize Known vs Unknown",
                    description="Summarize what is known, what remains uncertain, and what later implementation and verification work would likely be required.",
                    dependencies=["step-1"],
                    scope_hint=scope_manager.target_scope_hint(None),
                    expected_outcome="The plan stays honest about discovery needs and does not imply unavailable knowledge.",
                    phase="verify",
                    risk_hint="low",
                    approval_hint="included_in_run_approval",
                ),
            ]
        )

    @staticmethod
    def _verification_description(
        verification_config: VerificationConfig,
        action: TaskAction,
    ) -> str:
        if verification_config.profile.value == "none":
            return "No post-execution verification is requested for this run."
        if verification_config.profile.value == "basic":
            if action in {TaskAction.CREATE_FILE, TaskAction.MODIFY_FILE}:
                return "Confirm the file now exists, that it was modified as expected, and that any configured verification commands also succeed."
            if action == TaskAction.RUN_COMMAND:
                return "Confirm the command succeeded and run any configured verification commands that should also pass."
            return "Confirm the main action outcome and any configured verification commands that are relevant."
        return "Run the explicit user-provided verification commands after execution."

    @staticmethod
    def _verification_outcome(
        verification_config: VerificationConfig,
        action: TaskAction,
    ) -> str:
        if verification_config.profile.value == "none":
            return "Verification is intentionally skipped after execution."
        if verification_config.profile.value == "basic":
            if action in {TaskAction.CREATE_FILE, TaskAction.MODIFY_FILE}:
                return "Built-in file existence and content checks are recorded, along with any configured verification commands."
            if action == TaskAction.RUN_COMMAND:
                return "The command success and any configured verification commands are recorded clearly."
            return "Relevant built-in checks and configured verification commands are recorded clearly."
        return "Each verification command result is captured structurally, including failures."

    @staticmethod
    def _repo_note_for_target(
        target: str, repo_index_summary: RepoIndexSummary | None
    ) -> str:
        if repo_index_summary is None or not repo_index_summary.generated:
            return ""
        directory = target.split("/")[0] if "/" in target else "."
        hints: list[str] = []
        if directory in repo_index_summary.top_level_directories:
            hints.append(f"the top-level directory '{directory}' already exists")
        if repo_index_summary.likely_test_files:
            hints.append("likely tests are already present")
        if not hints:
            return " Repo index does not confirm this exact target path yet, so the change remains discovery-first."
        return " Repo index suggests " + "; ".join(hints) + "."

    @staticmethod
    def _repo_context_phrase(repo_index_summary: RepoIndexSummary | None) -> str:
        if repo_index_summary is None or not repo_index_summary.generated:
            return ""
        hints: list[str] = []
        if repo_index_summary.likely_entry_files:
            hints.append(
                "likely entry files include "
                + ", ".join(repo_index_summary.likely_entry_files[:3])
            )
        if repo_index_summary.likely_config_files:
            hints.append(
                "likely config files include "
                + ", ".join(repo_index_summary.likely_config_files[:3])
            )
        if repo_index_summary.likely_test_files:
            hints.append(
                "likely test files include "
                + ", ".join(repo_index_summary.likely_test_files[:3])
            )
        if not hints:
            return " Repo index is available but does not strongly suggest relevant files yet."
        return " Repo index suggests " + "; ".join(hints) + "."

    @staticmethod
    def _generic_repo_discovery(repo_index_summary: RepoIndexSummary | None) -> str:
        if repo_index_summary is None or not repo_index_summary.generated:
            return ""
        hints: list[str] = []
        if repo_index_summary.top_level_directories:
            hints.append(
                "top-level directories include "
                + ", ".join(repo_index_summary.top_level_directories[:5])
            )
        if repo_index_summary.likely_test_files:
            hints.append(
                "likely test files include "
                + ", ".join(repo_index_summary.likely_test_files[:3])
            )
        if repo_index_summary.python_symbols:
            first_file = next(iter(repo_index_summary.python_symbols.keys()))
            hints.append(f"lightweight Python symbols were found in {first_file}")
        if not hints:
            return " Repo index is available but still leaves the relevant files uncertain."
        return " Repo index can guide discovery because " + "; ".join(hints) + "."
