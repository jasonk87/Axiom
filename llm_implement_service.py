from __future__ import annotations

import json
from typing import Any

from artifact_manager import ArtifactManager
from llm_client import LLMSettings
from llm_retry import StructuredOutputRetryEngine
from models import (
    LLMActivityEvent,
    LLMStructuredResult,
    ProjectMemoryContext,
    RepoIndexSummary,
    SubTask,
)
from scope_manager import ScopeManager


class LocalLLMImplementService:
    def __init__(self, settings: LLMSettings | None = None, provider=None) -> None:
        self.settings = settings or LLMSettings.from_env()
        self.provider = provider

    def enabled(self) -> bool:
        return self.settings.enabled

    def generate_implementation(
        self,
        artifact_manager: ArtifactManager,
        subtask: SubTask,
        scope_manager: ScopeManager,
        repo_index_summary: RepoIndexSummary | None,
        project_memory: ProjectMemoryContext | None,
        existing_content: str | None = None,
    ) -> tuple[str | None, LLMStructuredResult | None]:
        if not self.settings.enabled:
            return None, None

        engine = StructuredOutputRetryEngine(self.settings, artifact_manager, provider=self.provider)
        fallback_message = "Axiom failed to generate implementation for the subtask."

        if subtask.action in {"create_file", "modify_file"}:
            system_instruction = (
                "You are an expert coding assistant.\n"
                "Return only JSON.\n"
                "The top-level object must be {\"content\": \"...\"}.\n"
                "Provide the complete, exact file content based on the description. Do not wrap it in markdown block quotes inside the JSON."
            )
        elif subtask.action == "run_command":
            system_instruction = (
                "You are an expert shell assistant.\n"
                "Return only JSON.\n"
                "The top-level object must be {\"command\": \"...\"}.\n"
                "Provide the exact shell command to execute."
            )
        else:
            return None, None

        structured_context: dict[str, Any] = {
            "subtask": subtask.to_dict(),
            "workspace": {
                "scope": scope_manager.describe_effective_scope(),
                "protected_paths": scope_manager.protected_path_labels(),
            },
            "project_memory": (
                project_memory.to_dict()
                if project_memory
                else {"summary": "", "known_commands": [], "recent_context": ""}
            ),
            "repo_index_summary": repo_index_summary.to_dict() if repo_index_summary else {"generated": False},
        }
        if existing_content is not None:
             structured_context["existing_content"] = existing_content

        context_artifact = artifact_manager.save_json(
            artifact_manager.build_filename("llm_implement_context"),
            structured_context,
        )
        user_instruction = json.dumps(structured_context, indent=2, sort_keys=True)
        context_events = [
            LLMActivityEvent(
                stage="apply_project_context",
                status="completed",
                summary=(
                    "Applied project context from project memory before asking the local model to implement."
                    if project_memory
                    else "No project memory was available, so Axiom used an empty project context."
                ),
                details={"project_memory": structured_context["project_memory"]},
                artifact_reference=context_artifact,
            )
        ]

        # We define a custom validator inline
        from models import LLMValidationIssue
        def validate_impl(payload: dict) -> list[LLMValidationIssue]:
             issues = []
             if subtask.action in {"create_file", "modify_file"}:
                  if "content" not in payload:
                       issues.append(LLMValidationIssue(code="missing_required_field", message="Missing 'content' field.", path="content"))
                  elif not isinstance(payload["content"], str):
                       issues.append(LLMValidationIssue(code="wrong_type", message="'content' must be a string.", path="content"))
             else:
                  if "command" not in payload:
                       issues.append(LLMValidationIssue(code="missing_required_field", message="Missing 'command' field.", path="command"))
                  elif not isinstance(payload["command"], str):
                       issues.append(LLMValidationIssue(code="wrong_type", message="'command' must be a string.", path="command"))
             return issues

        result = engine.generate_structured_output(
            feature="task_implementation",
            output_label="task implementation",
            system_instruction=system_instruction,
            user_instruction=user_instruction,
            fallback_message=fallback_message,
            validator=validate_impl,
            artifact_prefix="llm_implement",
            initial_events=context_events,
            initial_artifacts=[context_artifact],
        )
        if result.payload is None:
            return None, result.summary

        if subtask.action in {"create_file", "modify_file"}:
             return result.payload["content"], result.summary
        else:
             return result.payload["command"], result.summary
