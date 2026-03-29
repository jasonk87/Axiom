from __future__ import annotations

from models import (
    BlockedAction,
    CommandResult,
    FailureCategory,
    FailureClassification,
    StepResult,
    StepType,
)


class FailureClassifier:
    def classify(
        self,
        execution_message: str,
        step_results: list[StepResult],
        blocked_actions: list[BlockedAction],
        commands_run: list[CommandResult],
    ) -> FailureClassification:
        message_lower = execution_message.lower()

        if "command policy blocked" in message_lower:
            return FailureClassification(
                category=FailureCategory.COMMAND_POLICY_BLOCK,
                reason=execution_message,
                source="command_policy",
            )

        if blocked_actions:
            blocked = blocked_actions[-1]
            reason_lower = blocked.reason.lower()
            if "protected path" in reason_lower:
                return FailureClassification(
                    category=FailureCategory.PROTECTED_PATH_VIOLATION,
                    reason=blocked.reason,
                    source="write_policy",
                )
            if "outside the active scope" in reason_lower:
                return FailureClassification(
                    category=FailureCategory.SCOPE_VIOLATION,
                    reason=blocked.reason,
                    source="scope_control",
                )
            return FailureClassification(
                category=FailureCategory.WRITE_POLICY_BLOCK,
                reason=blocked.reason,
                source="write_policy",
            )

        verification_failure = next(
            (
                step
                for step in step_results
                if step.step_type == StepType.VERIFICATION
                and step.status.value == "failed"
            ),
            None,
        )
        if verification_failure is not None:
            return FailureClassification(
                category=FailureCategory.VERIFICATION_FAILURE,
                reason=verification_failure.message,
                source="verification",
            )

        failed_command = next(
            (command for command in reversed(commands_run) if not command.success), None
        )
        if failed_command is not None:
            return FailureClassification(
                category=FailureCategory.COMMAND_EXECUTION_FAILURE,
                reason=failed_command.summary,
                source="command_execution",
            )

        if "parse" in message_lower or "analysis" in message_lower:
            return FailureClassification(
                category=FailureCategory.PARSE_OR_ANALYSIS_FAILURE,
                reason=execution_message,
                source="analysis",
            )

        return FailureClassification(
            category=FailureCategory.UNKNOWN_FAILURE,
            reason=execution_message,
            source="unknown",
        )
