from __future__ import annotations

import json

from artifact_manager import ArtifactManager
from llm_client import LLMSettings
from llm_retry import StructuredOutputRetryEngine
from models import (
    LLMActivityEvent,
    LLMStructuredResult,
    Plan,
    ProjectMemoryContext,
    RepoIndexSummary,
    TaskInterpretation,
    VerificationConfig,
)
from scope_manager import ScopeManager


class LocalLLMReviewService:
    def __init__(self, settings: LLMSettings | None = None, provider=None) -> None:
        self.settings = settings or LLMSettings.from_env()
        self.provider = provider

    def enabled(self) -> bool:
        return self.settings.enabled and self.settings.review_enabled

    def review_plan(
        self,
        artifact_manager: ArtifactManager,
        interpretation: TaskInterpretation,
        scope_manager: ScopeManager,
        verification: VerificationConfig,
        repo_index_summary: RepoIndexSummary | None,
        project_memory: ProjectMemoryContext | None,
        plan: Plan,
    ) -> LLMStructuredResult | None:
        if not self.enabled():
            return None

        engine = StructuredOutputRetryEngine(
            self.settings, artifact_manager, provider=self.provider
        )
        fallback_message = "Axiom kept the accepted plan and ignored the unavailable or invalid review output."
        system_instruction = (
            "You are reviewing a structured execution plan for a controlled coding workbench.\n"
            "Return only JSON.\n"
            "The top-level object must contain exactly these keys: "
            "verdict, summary, scope_ok, overbuild_detected, findings, suggested_adjustments.\n"
            "verdict must be one of accept, revise, caution.\n"
            "Each finding must contain exactly these keys: category, message, severity, step_ids.\n"
            "severity must be one of low, medium, high.\n"
            "Be concise, honest, and advisory. Do not invent repository facts.\n"
            "Review for scope drift, unnecessary complexity, missing obvious verification, and risky ambiguity."
        )
        structured_context = {
            "task": {
                "summary": interpretation.summary,
                "raw_task": interpretation.raw_task,
            },
            "workspace": {
                "scope": scope_manager.describe_effective_scope(),
                "protected_paths": scope_manager.protected_path_labels(),
                "verification_profile": verification.profile.value,
            },
            "project_memory": (
                project_memory.to_dict()
                if project_memory
                else {"summary": "", "known_commands": [], "recent_context": ""}
            ),
            "repo_index_summary": (
                repo_index_summary.to_dict()
                if repo_index_summary
                else {"generated": False}
            ),
            "plan": plan.to_dict(),
            "instruction": "Review whether the plan is appropriately scoped and not overbuilt for the request.",
        }
        context_artifact = artifact_manager.save_json(
            artifact_manager.build_filename("llm_review_context"),
            structured_context,
        )
        user_instruction = json.dumps(structured_context, indent=2, sort_keys=True)
        context_events = [
            LLMActivityEvent(
                stage="apply_project_context",
                status="completed",
                summary=(
                    "Applied project context before reviewing the accepted plan."
                    if project_memory
                    else "No project memory was available, so Axiom reviewed with an empty project context."
                ),
                details={"project_memory": structured_context["project_memory"]},
                artifact_reference=context_artifact,
            )
        ]
        result = engine.generate_review_output(
            system_instruction,
            user_instruction,
            fallback_message,
            initial_events=context_events,
            initial_artifacts=[context_artifact],
        )
        return result.summary
