from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from artifact_manager import ArtifactManager
from llm_client import LLMProviderError, LLMResponse, LLMSettings, build_provider
from models import ArtifactReference, LLMActivityEvent, LLMStructuredResult, LLMValidationIssue
from structured_output import parse_json_object, validate_plan_payload, validate_review_payload, validate_decompose_payload, validate_replan_payload


@dataclass
class StructuredPlanOutput:
    payload: dict | None
    summary: LLMStructuredResult


class StructuredOutputRetryEngine:
    def __init__(self, settings: LLMSettings, artifact_manager: ArtifactManager, provider=None) -> None:
        self.settings = settings
        self.artifact_manager = artifact_manager
        self.provider = provider

    def generate_plan_output(
        self,
        system_instruction: str,
        user_instruction: str,
        fallback_message: str,
        initial_events: list[LLMActivityEvent] | None = None,
        initial_artifacts: list[ArtifactReference] | None = None,
    ) -> StructuredPlanOutput:
        return self.generate_structured_output(
            feature="plan_generation",
            output_label="structured plan",
            system_instruction=system_instruction,
            user_instruction=user_instruction,
            fallback_message=fallback_message,
            validator=validate_plan_payload,
            artifact_prefix="llm_plan",
            initial_events=initial_events,
            initial_artifacts=initial_artifacts,
        )

    def generate_review_output(
        self,
        system_instruction: str,
        user_instruction: str,
        fallback_message: str,
        initial_events: list[LLMActivityEvent] | None = None,
        initial_artifacts: list[ArtifactReference] | None = None,
    ) -> StructuredPlanOutput:
        return self.generate_structured_output(
            feature="plan_review",
            output_label="structured review",
            system_instruction=system_instruction,
            user_instruction=user_instruction,
            fallback_message=fallback_message,
            validator=validate_review_payload,
            artifact_prefix="llm_review",
            initial_events=initial_events,
            initial_artifacts=initial_artifacts,
        )

    def generate_decompose_output(
        self,
        system_instruction: str,
        user_instruction: str,
        fallback_message: str,
        initial_events: list[LLMActivityEvent] | None = None,
        initial_artifacts: list[ArtifactReference] | None = None,
    ) -> StructuredPlanOutput:
        return self.generate_structured_output(
            feature="task_decomposition",
            output_label="task decomposition",
            system_instruction=system_instruction,
            user_instruction=user_instruction,
            fallback_message=fallback_message,
            validator=validate_decompose_payload,
            artifact_prefix="llm_decompose",
            initial_events=initial_events,
            initial_artifacts=initial_artifacts,
        )

    def generate_replan_output(
        self,
        system_instruction: str,
        user_instruction: str,
        fallback_message: str,
        initial_events: list[LLMActivityEvent] | None = None,
        initial_artifacts: list[ArtifactReference] | None = None,
    ) -> StructuredPlanOutput:
        return self.generate_structured_output(
            feature="task_replanning",
            output_label="task replanning",
            system_instruction=system_instruction,
            user_instruction=user_instruction,
            fallback_message=fallback_message,
            validator=validate_replan_payload,
            artifact_prefix="llm_replan",
            initial_events=initial_events,
            initial_artifacts=initial_artifacts,
        )

    def generate_structured_output(
        self,
        feature: str,
        output_label: str,
        system_instruction: str,
        user_instruction: str,
        fallback_message: str,
        validator: Callable[[dict], list[LLMValidationIssue]],
        artifact_prefix: str,
        initial_events: list[LLMActivityEvent] | None = None,
        initial_artifacts: list[ArtifactReference] | None = None,
    ) -> StructuredPlanOutput:
        provider = self.provider or build_provider(self.settings)
        events: list[LLMActivityEvent] = list(initial_events or [])
        artifacts: list[ArtifactReference] = list(initial_artifacts or [])
        current_user_instruction = user_instruction
        max_attempts = self.settings.retry_limit + 1

        for attempt in range(1, max_attempts + 1):
            try:
                response = provider.generate(system_instruction, current_user_instruction)
            except LLMProviderError as caught:
                events.append(
                    LLMActivityEvent(
                        stage="query_model",
                        status="failed",
                        summary=f"Local model call failed while requesting {output_label} on attempt {attempt}/{max_attempts}.",
                        attempt=attempt,
                        details={"code": caught.code, "message": caught.message},
                    )
                )
                return StructuredPlanOutput(
                    payload=None,
                    summary=LLMStructuredResult(
                        feature=feature,
                        provider=self.settings.provider,
                        model=self.settings.model,
                        enabled=True,
                        accepted=False,
                        fallback_used=True,
                        attempts_used=attempt,
                        retry_limit=self.settings.retry_limit,
                        accepted_payload=None,
                        final_message=f"{caught.message} {fallback_message}",
                        events=events + [
                            LLMActivityEvent(
                                stage="fallback",
                                status="completed",
                                summary=f"Falling back after the local model failed to return a usable {output_label}.",
                                attempt=attempt,
                            )
                        ],
                        artifact_references=artifacts,
                    ),
                )

            attempt_artifact = self.artifact_manager.save_json(
                self.artifact_manager.build_filename(f"{artifact_prefix}_attempt_{attempt}"),
                {
                    "feature": feature,
                    "attempt": attempt,
                    "provider": response.provider,
                    "model": response.model,
                    "raw_text": response.raw_text,
                    "metadata": response.metadata,
                },
            )
            artifacts.append(attempt_artifact)
            events.append(
                LLMActivityEvent(
                    stage="query_model",
                    status="completed",
                    summary=f"Asked local model for {output_label} (attempt {attempt}/{max_attempts}).",
                    attempt=attempt,
                    details={"raw_preview": response.raw_text[:600]},
                    artifact_reference=attempt_artifact,
                )
            )

            payload, parse_issues = parse_json_object(response.raw_text)
            validation_issues = parse_issues if parse_issues else validator(payload or {})
            if not validation_issues:
                events.append(
                    LLMActivityEvent(
                        stage="validate_response",
                        status="completed",
                        summary=f"Accepted the {output_label} on attempt {attempt}/{max_attempts}.",
                        attempt=attempt,
                    )
                )
                return StructuredPlanOutput(
                    payload=payload,
                    summary=LLMStructuredResult(
                        feature=feature,
                        provider=response.provider,
                        model=response.model,
                        enabled=True,
                        accepted=True,
                        fallback_used=False,
                        attempts_used=attempt,
                        retry_limit=self.settings.retry_limit,
                        accepted_payload=payload,
                        final_message=f"Local model {output_label} accepted after validation.",
                        events=events,
                        artifact_references=artifacts,
                    ),
                )

            validation_artifact = self.artifact_manager.save_json(
                self.artifact_manager.build_filename(f"{artifact_prefix}_validation_{attempt}"),
                {
                    "feature": feature,
                    "attempt": attempt,
                    "issues": [issue.to_dict() for issue in validation_issues],
                    "raw_text_preview": response.raw_text[:1000],
                },
            )
            artifacts.append(validation_artifact)
            events.append(
                LLMActivityEvent(
                    stage="validate_response",
                    status="failed",
                    summary=f"Model {output_label} failed validation on attempt {attempt}/{max_attempts}.",
                    attempt=attempt,
                    details={"issues": [issue.to_dict() for issue in validation_issues]},
                    artifact_reference=validation_artifact,
                )
            )

            if attempt == max_attempts:
                break

            current_user_instruction = self._build_retry_instruction(user_instruction, validation_issues)
            events.append(
                LLMActivityEvent(
                    stage="retry_output",
                    status="running",
                    summary=f"Retrying {output_label} (attempt {attempt + 1}/{max_attempts}) with specific correction feedback.",
                    attempt=attempt + 1,
                    details={"issues": [issue.to_dict() for issue in validation_issues]},
                )
            )

        events.append(
            LLMActivityEvent(
                stage="fallback",
                status="completed",
                summary=f"Local model retries were exhausted for {output_label}. Using fallback behavior.",
                attempt=max_attempts,
            )
        )
        return StructuredPlanOutput(
            payload=None,
            summary=LLMStructuredResult(
                feature=feature,
                provider=self.settings.provider,
                model=self.settings.model,
                enabled=True,
                accepted=False,
                fallback_used=True,
                attempts_used=max_attempts,
                retry_limit=self.settings.retry_limit,
                accepted_payload=None,
                final_message=f"Structured output retries were exhausted for {output_label}. {fallback_message}",
                events=events,
                artifact_references=artifacts,
            ),
        )

    @staticmethod
    def _build_retry_instruction(user_instruction: str, issues: list[LLMValidationIssue]) -> str:
        feedback_lines = "\n".join(
            f"- {issue.code} at {issue.path or '$'}: {issue.message}" for issue in issues
        )
        return (
            f"{user_instruction}\n\n"
            "Your previous response was invalid. Return only a JSON object that satisfies the required schema.\n"
            "Fix these issues exactly:\n"
            f"{feedback_lines}"
        )
