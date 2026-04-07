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
    TaskInterpretation,
    VerificationConfig,
)
from scope_manager import ScopeManager


class LocalLLMEvalService:
    def __init__(self, settings: LLMSettings | None = None, provider=None) -> None:
        self.settings = settings or LLMSettings.from_env()
        self.provider = provider

    def enabled(self) -> bool:
        return self.settings.enabled

    def generate_eval(
        self,
        artifact_manager: ArtifactManager,
        interpretation: TaskInterpretation,
        subtask: SubTask,
        scope_manager: ScopeManager,
        verification: VerificationConfig,
        repo_index_summary: RepoIndexSummary | None,
        project_memory: ProjectMemoryContext | None,
    ) -> tuple[dict[str, Any] | None, LLMStructuredResult | None]:
        if not self.settings.enabled:
            return None, None

        engine = StructuredOutputRetryEngine(
            self.settings, artifact_manager, provider=self.provider
        )
        fallback_message = "Axiom failed to evaluate the subtask."
        system_instruction = (
            "You are evaluating the completion status of a specific subtask.\n"
            "Return only JSON.\n"
            'The top-level object must be {"success": bool, "reasoning": "..."}.\n'
            "Read the subtask description, its target (if any), its command (if any), and the result summary.\n"
            "If the subtask appears to have accomplished its goal based on the result, set success to true.\n"
            "If the subtask failed or did not meet the objective, set success to false and provide reasoning."
        )
        structured_context = {
            "overall_task": interpretation.summary,
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
            "instruction": "Evaluate if this subtask was successful. Be honest and concise.",
        }
        context_artifact = artifact_manager.save_json(
            artifact_manager.build_filename("llm_eval_context"),
            structured_context,
        )
        user_instruction = json.dumps(structured_context, indent=2, sort_keys=True)
        context_events = [
            LLMActivityEvent(
                stage="apply_project_context",
                status="completed",
                summary=(
                    "Applied project context before evaluating the subtask."
                ),
                details={"project_memory": structured_context["project_memory"]},
                artifact_reference=context_artifact,
            )
        ]
        result = engine.generate_eval_output(
            system_instruction,
            user_instruction,
            fallback_message,
            initial_events=context_events,
            initial_artifacts=[context_artifact],
        )
        if result.payload is None:
            return None, result.summary

        return result.payload, result.summary
