from __future__ import annotations

from models import (
    PhasePolicy,
    PhasePower,
    Plan,
    StepType,
    TaskAction,
    TaskInterpretation,
)


def classify_plan_phases(
    plan: Plan | None, interpretation: TaskInterpretation
) -> list[PhasePolicy]:
    if plan is None:
        return []

    ordered_phases: list[str] = []
    for step in plan.steps:
        if step.phase not in ordered_phases:
            ordered_phases.append(step.phase)

    policies: list[PhasePolicy] = []
    for phase in ordered_phases:
        steps = [step for step in plan.steps if step.phase == phase]
        step_types = {step.step_type for step in steps}
        has_execution = StepType.EXECUTION in step_types
        has_discovery = StepType.DISCOVERY in step_types
        has_verification = StepType.VERIFICATION in step_types

        if step_types == {StepType.DISCOVERY}:
            policies.append(
                PhasePolicy(
                    phase=phase,
                    classification=PhasePower.READ_ONLY,
                    approval_required=False,
                    auto_run_allowed=True,
                    reason="This phase only contains discovery steps, so the system classified it as read-only.",
                )
            )
            continue

        if step_types == {StepType.VERIFICATION}:
            policies.append(
                PhasePolicy(
                    phase=phase,
                    classification=PhasePower.VERIFY_ONLY,
                    approval_required=True,
                    auto_run_allowed=False,
                    reason="Verification stays approval-gated in this phase to preserve the current trust model.",
                )
            )
            continue

        if has_execution and interpretation.action in {
            TaskAction.CREATE_FILE,
            TaskAction.MODIFY_FILE,
            TaskAction.RESTORE_SNAPSHOT,
        }:
            policies.append(
                PhasePolicy(
                    phase=phase,
                    classification=PhasePower.WRITES_FILES,
                    approval_required=True,
                    auto_run_allowed=False,
                    reason="This phase can modify workspace files, so it must remain approval-gated.",
                )
            )
            continue

        if has_execution and interpretation.action == TaskAction.RUN_COMMAND:
            policies.append(
                PhasePolicy(
                    phase=phase,
                    classification=PhasePower.RUNS_COMMANDS,
                    approval_required=True,
                    auto_run_allowed=False,
                    reason="This phase can run shell commands, so it must remain approval-gated.",
                )
            )
            continue

        if has_discovery and has_verification and not has_execution:
            policies.append(
                PhasePolicy(
                    phase=phase,
                    classification=PhasePower.MIXED,
                    approval_required=True,
                    auto_run_allowed=False,
                    reason="This phase mixes multiple step powers, so the system falls back to gated execution.",
                )
            )
            continue

        policies.append(
            PhasePolicy(
                phase=phase,
                classification=PhasePower.MIXED,
                approval_required=True,
                auto_run_allowed=False,
                reason="This phase could not be proven read-only, so the system keeps it approval-gated.",
            )
        )

    return policies
