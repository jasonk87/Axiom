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


class LocalLLMReplanService:
    def __init__(self, settings: LLMSettings | None = None, provider=None) -> None:
        self.settings = settings or LLMSettings.from_env()
        self.provider = provider

    def enabled(self) -> bool:
        return self.settings.enabled

    def generate_replan(
        self,
        artifact_manager: ArtifactManager,
        interpretation: TaskInterpretation,
        completed_subtasks: list[SubTask],
        scope_manager: ScopeManager,
        verification: VerificationConfig,
        repo_index_summary: RepoIndexSummary | None,
        project_memory: ProjectMemoryContext | None,
    ) -> tuple[dict[str, Any] | None, LLMStructuredResult | None]:
        if not self.settings.enabled:
            return None, None

        engine = StructuredOutputRetryEngine(self.settings, artifact_manager, provider=self.provider)
        fallback_message = "Axiom failed to replan the remaining work."
        system_instruction = (
            "You are evaluating the progress of a complex coding request for a local model.\n"
            "Return only JSON.\n"
            "The top-level object must be {\"is_complete\": bool, \"reasoning\": \"...\", \"new_subtasks\": [...]}.\n"
            "If the original task goal is fully accomplished, set is_complete to true and new_subtasks to an empty list.\n"
            "If more work is needed, set is_complete to false, and provide the next necessary subtasks.\n"
            "Each subtask must contain exactly these keys: action, description, target_path, command.\n"
            "Action must be one of: create_file, modify_file, run_command, analyze, restore_snapshot, unknown.\n"
            "If target_path or command are not applicable, set them to null."
        )
        structured_context = {
            "task": {
                "summary": interpretation.summary,
                "raw_task": interpretation.raw_task,
            },
            "completed_subtasks": [st.to_dict() for st in completed_subtasks],
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
            "instruction": "Evaluate if the overall task is complete based on the subtask results. If not, generate the next subtasks.",
        }
        context_artifact = artifact_manager.save_json(
            artifact_manager.build_filename("llm_replan_context"),
            structured_context,
        )
        user_instruction = json.dumps(structured_context, indent=2, sort_keys=True)
        context_events = [
            LLMActivityEvent(
                stage="apply_project_context",
                status="completed",
                summary=(
                    "Applied project context from project memory before asking the local model to replan."
                    if project_memory
                    else "No project memory was available, so Axiom used an empty project context."
                ),
                details={"project_memory": structured_context["project_memory"]},
                artifact_reference=context_artifact,
            )
        ]
        result = engine.generate_replan_output(
            system_instruction,
            user_instruction,
            fallback_message,
            initial_events=context_events,
            initial_artifacts=[context_artifact],
        )
        if result.payload is None:
            return None, result.summary

        return result.payload, result.summary
