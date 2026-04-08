from __future__ import annotations

import json

from artifact_manager import ArtifactManager
from llm_client import LLMSettings
from llm_retry import StructuredOutputRetryEngine
from models import (
    LLMActivityEvent,
    LLMStructuredResult,
    LLMValidationIssue,
    ProjectMemoryContext,
    TaskInterpretation,
)


class LocalLLMMemoryService:
    def __init__(self, settings: LLMSettings | None = None, provider=None) -> None:
        self.settings = settings or LLMSettings.from_env()
        self.provider = provider

    def generate_memory_update(
        self,
        artifact_manager: ArtifactManager,
        interpretation: TaskInterpretation,
        current_memory: ProjectMemoryContext,
        execution_summary: str,
    ) -> tuple[ProjectMemoryContext | None, LLMStructuredResult | None]:
        if not self.settings.enabled:
            return None, None

        engine = StructuredOutputRetryEngine(
            self.settings, artifact_manager, provider=self.provider
        )
        fallback_message = "Axiom failed to update project memory."

        system_instruction = (
            "You are an AI assistant tasked with updating the project memory ledger.\n"
            "Return only JSON.\n"
            'The top-level object must have exactly these keys: "project_summary" (string), "recent_context" (string), and "known_commands" (list of strings).\n'
            "Update the summary with any high-level architectural knowledge gained.\n"
            "Update the recent context to reflect the latest completed task.\n"
            "Add any new, useful commands to known_commands."
        )

        structured_context = {
            "task": interpretation.raw_task,
            "execution_summary": execution_summary,
            "current_memory": current_memory.to_dict(),
        }
        context_artifact = artifact_manager.save_json(
            artifact_manager.build_filename("llm_memory_context"),
            structured_context,
        )
        user_instruction = json.dumps(structured_context, indent=2, sort_keys=True)
        context_events = [
            LLMActivityEvent(
                stage="apply_project_context",
                status="completed",
                summary="Loaded previous project memory for update.",
                details={"current_memory": structured_context["current_memory"]},
                artifact_reference=context_artifact,
            )
        ]

        def validate_memory(payload: dict) -> list[LLMValidationIssue]:
            issues = []
            if "project_summary" not in payload:
                issues.append(
                    LLMValidationIssue(
                        code="missing_field",
                        message="Missing 'project_summary'",
                        path="project_summary",
                    )
                )
            if "recent_context" not in payload:
                issues.append(
                    LLMValidationIssue(
                        code="missing_field",
                        message="Missing 'recent_context'",
                        path="recent_context",
                    )
                )
            if "known_commands" not in payload:
                issues.append(
                    LLMValidationIssue(
                        code="missing_field",
                        message="Missing 'known_commands'",
                        path="known_commands",
                    )
                )
            return issues

        result = engine.generate_structured_output(
            feature="project_memory_update",
            output_label="project memory update",
            system_instruction=system_instruction,
            user_instruction=user_instruction,
            fallback_message=fallback_message,
            validator=validate_memory,
            artifact_prefix="llm_memory",
            initial_events=context_events,
            initial_artifacts=[context_artifact],
        )
        if result.payload is None:
            return None, result.summary

        new_memory = ProjectMemoryContext(
            summary=result.payload.get("project_summary", current_memory.summary),
            recent_context=result.payload.get("recent_context", current_memory.recent_context),
            known_commands=result.payload.get("known_commands", current_memory.known_commands),
        )
        return new_memory, result.summary
