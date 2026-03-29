from __future__ import annotations

import json

from artifact_manager import ArtifactManager
from llm_client import LLMSettings
from llm_retry import StructuredOutputRetryEngine
from models import (
    LLMActivityEvent,
    LLMStructuredResult,
    Plan,
    PlanStep,
    ProjectMemoryContext,
    RepoIndexSummary,
    StepType,
    TaskInterpretation,
    VerificationConfig,
)
from scope_manager import ScopeManager


class LocalLLMPlanService:
    def __init__(self, settings: LLMSettings | None = None, provider=None) -> None:
        self.settings = settings or LLMSettings.from_env()
        self.provider = provider

    def enabled(self) -> bool:
        return self.settings.enabled

    def generate_plan(
        self,
        artifact_manager: ArtifactManager,
        interpretation: TaskInterpretation,
        scope_manager: ScopeManager,
        verification: VerificationConfig,
        repo_index_summary: RepoIndexSummary | None,
        project_memory: ProjectMemoryContext | None,
    ) -> tuple[Plan | None, LLMStructuredResult | None]:
        if not self.settings.enabled:
            return None, None

        engine = StructuredOutputRetryEngine(
            self.settings, artifact_manager, provider=self.provider
        )
        fallback_message = "Axiom used the built-in planner instead of trusting an invalid model response."
        system_instruction = (
            "You are producing a strict JSON plan object for a controlled coding workbench.\n"
            "Return only JSON.\n"
            'The top-level object must be {"steps": [...]}.\n'
            "Each step must contain exactly these keys: "
            "id, type, title, description, dependencies, scope_hint, expected_outcome, phase, risk_hint, approval_hint.\n"
            "Use type in {discovery, execution, verification}.\n"
            "Be honest about uncertainty. If the repo context is incomplete, add discovery steps instead of pretending certainty."
        )
        structured_context = {
            "task": {
                "summary": interpretation.summary,
                "raw_task": interpretation.raw_task,
                "action": interpretation.action.value,
                "target_path": interpretation.target_path,
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
            "instruction": "Return a small practical plan for this request.",
        }
        context_artifact = artifact_manager.save_json(
            artifact_manager.build_filename("llm_plan_context"),
            structured_context,
        )
        user_instruction = json.dumps(structured_context, indent=2, sort_keys=True)
        context_events = [
            LLMActivityEvent(
                stage="apply_project_context",
                status="completed",
                summary=(
                    "Applied project context from project memory before asking the local model."
                    if project_memory
                    else "No project memory was available, so Axiom used an empty project context."
                ),
                details={"project_memory": structured_context["project_memory"]},
                artifact_reference=context_artifact,
            )
        ]
        result = engine.generate_plan_output(
            system_instruction,
            user_instruction,
            fallback_message,
            initial_events=context_events,
            initial_artifacts=[context_artifact],
        )
        if result.payload is None:
            return None, result.summary
        return self._plan_from_payload(result.payload), result.summary

    @staticmethod
    def _plan_from_payload(payload: dict) -> Plan:
        return Plan(
            steps=[
                PlanStep(
                    id=step["id"],
                    step_type=StepType(step["type"]),
                    title=step["title"],
                    description=step["description"],
                    dependencies=list(step["dependencies"]),
                    scope_hint=step["scope_hint"],
                    expected_outcome=step["expected_outcome"],
                    phase=step["phase"],
                    risk_hint=step["risk_hint"],
                    approval_hint=step["approval_hint"],
                )
                for step in payload["steps"]
            ]
        )
