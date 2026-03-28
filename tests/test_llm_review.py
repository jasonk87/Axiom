from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from artifact_manager import ArtifactManager
from llm_client import LLMProviderError, LLMResponse, LLMSettings
from llm_retry import StructuredOutputRetryEngine
from llm_review_service import LocalLLMReviewService
from models import Mode, Plan, PlanStep, ProjectMemoryContext, RepoIndexSummary, StepType, TaskAction, TaskInterpretation, VerificationConfig, VerificationProfile
from orchestrator import Orchestrator
from scope_manager import ScopeManager


VALID_REVIEW_JSON = """
{
  "verdict": "accept",
  "summary": "The plan is appropriately scoped and concise.",
  "scope_ok": true,
  "overbuild_detected": false,
  "findings": [
    {
      "category": "confirmation",
      "message": "The plan stays within the requested file scope.",
      "severity": "low",
      "step_ids": ["step-1"]
    }
  ],
  "suggested_adjustments": []
}
""".strip()


class FakeProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def generate(self, system_instruction: str, user_instruction: str) -> LLMResponse:
        if not self.responses:
            raise AssertionError("No more fake responses were configured.")
        self.calls.append({"system": system_instruction, "user": user_instruction})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return LLMResponse(raw_text=response, provider="fake", model="fake-model", metadata={})


class FakePlanService:
    def __init__(self, plan: Plan, summary) -> None:
        self.plan = plan
        self.summary = summary

    def enabled(self) -> bool:
        return True

    def generate_plan(self, **kwargs):
        return self.plan, self.summary


class FakeReviewService:
    def __init__(self, summary) -> None:
        self.summary = summary

    def enabled(self) -> bool:
        return True

    def review_plan(self, **kwargs):
        return self.summary


class LLMReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temp_dir.name)
        self.artifact_manager = ArtifactManager(str(self.project_root))
        self.settings = LLMSettings(
            enabled=True,
            review_enabled=True,
            provider="ollama",
            base_url="http://127.0.0.1:11434",
            model="test-model",
            timeout_seconds=5,
            retry_limit=2,
            temperature=0.1,
            compression_enabled=True,
            compression_threshold=5,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _engine(self, responses):
        provider = FakeProvider(responses)
        return StructuredOutputRetryEngine(
            settings=self.settings,
            artifact_manager=self.artifact_manager,
            provider=provider,
        ), provider

    def test_valid_review_json_accepted_first_try(self) -> None:
        engine, _ = self._engine([VALID_REVIEW_JSON])
        result = engine.generate_review_output("system", "user", "fallback")
        self.assertIsNotNone(result.payload)
        self.assertTrue(result.summary.accepted)
        self.assertEqual(result.summary.accepted_payload["verdict"], "accept")

    def test_malformed_review_json_retried_then_accepted(self) -> None:
        engine, _ = self._engine(["not-json", VALID_REVIEW_JSON])
        result = engine.generate_review_output("system", "user", "fallback")
        self.assertTrue(result.summary.accepted)
        self.assertEqual(result.summary.attempts_used, 2)

    def test_invalid_review_schema_retried_then_accepted(self) -> None:
        wrong_schema = '{"verdict":"accept","summary":"ok","scope_ok":true,"overbuild_detected":false,"findings":"bad","suggested_adjustments":[]}'
        engine, _ = self._engine([wrong_schema, VALID_REVIEW_JSON])
        result = engine.generate_review_output("system", "user", "fallback")
        self.assertTrue(result.summary.accepted)
        self.assertTrue(any(event.stage == "validate_response" and event.status == "failed" for event in result.summary.events))

    def test_review_retry_exhaustion_falls_back_cleanly(self) -> None:
        engine, _ = self._engine(["{}", "{}", "{}"])
        result = engine.generate_review_output("system", "user", "fallback")
        self.assertFalse(result.summary.accepted)
        self.assertTrue(result.summary.fallback_used)
        self.assertIn("fallback", result.summary.final_message.lower())

    def test_review_fallback_does_not_break_accepted_plan_flow(self) -> None:
        plan = Plan(
            steps=[
                PlanStep(
                    id="step-1",
                    step_type=StepType.DISCOVERY,
                    title="Inspect target",
                    description="Read the target file first.",
                    dependencies=[],
                    scope_hint="src/app.py",
                    expected_outcome="Target file understood.",
                    phase="understand",
                    risk_hint="low",
                    approval_hint="included_in_run_approval",
                )
            ]
        )
        plan_engine, _ = self._engine([
            """
            {
              "steps": [
                {
                  "id": "step-1",
                  "type": "discovery",
                  "title": "Inspect target",
                  "description": "Read the target file first.",
                  "dependencies": [],
                  "scope_hint": "src/app.py",
                  "expected_outcome": "Target file understood.",
                  "phase": "understand",
                  "risk_hint": "low",
                  "approval_hint": "included_in_run_approval"
                }
              ]
            }
            """.strip()
        ])
        plan_summary = plan_engine.generate_plan_output("system", "user", "fallback").summary
        review_engine, _ = self._engine(["{}", "{}", "{}"])
        review_summary = review_engine.generate_review_output("system", "user", "fallback").summary

        orchestrator = Orchestrator(str(self.project_root), llm_settings=self.settings)
        orchestrator.local_llm_plan_service = FakePlanService(plan, plan_summary)
        orchestrator.local_llm_review_service = FakeReviewService(review_summary)
        interpretation = TaskInterpretation(
            raw_task="Plan a safe file update.",
            summary="Plan a safe file update.",
            action=TaskAction.MODIFY_FILE,
            target_path="src/app.py",
            content="print('hi')",
        )
        scope_manager = ScopeManager(str(self.project_root), ["src"], [])
        built_plan, _, built_review = orchestrator.build_plan_with_local_llm(
            mode=Mode.IMPLEMENT,
            interpretation=interpretation,
            scope_manager=scope_manager,
            verification=VerificationConfig(profile=VerificationProfile.BASIC, commands=[]),
            repo_index_summary=RepoIndexSummary(generated=False),
            project_memory=None,
            artifact_manager=self.artifact_manager,
            artifact_references=[],
        )
        self.assertIsNotNone(built_plan)
        self.assertIsNotNone(built_review)
        self.assertTrue(built_review.fallback_used)

    def test_provider_unavailable_review_falls_back_cleanly(self) -> None:
        error = LLMProviderError("provider_unavailable", "Ollama is unavailable.")
        engine, _ = self._engine([error])
        result = engine.generate_review_output("system", "user", "fallback")
        self.assertFalse(result.summary.accepted)
        self.assertTrue(result.summary.fallback_used)

    def test_review_uses_project_memory_context(self) -> None:
        provider = FakeProvider([VALID_REVIEW_JSON])
        service = LocalLLMReviewService(settings=self.settings, provider=provider)
        interpretation = TaskInterpretation(
            raw_task="Review this plan",
            summary="Review this plan",
            action=TaskAction.MODIFY_FILE,
            target_path="src/app.py",
            content="print('hi')",
        )
        plan = Plan(
            steps=[
                PlanStep(
                    id="step-1",
                    step_type=StepType.DISCOVERY,
                    title="Inspect target",
                    description="Read the target file first.",
                    dependencies=[],
                    scope_hint="src/app.py",
                    expected_outcome="Target file understood.",
                    phase="understand",
                    risk_hint="low",
                    approval_hint="included_in_run_approval",
                )
            ]
        )
        summary = service.review_plan(
            artifact_manager=self.artifact_manager,
            interpretation=interpretation,
            scope_manager=ScopeManager(str(self.project_root), ["src"], []),
            verification=VerificationConfig(profile=VerificationProfile.BASIC, commands=[]),
            repo_index_summary=RepoIndexSummary(generated=False),
            project_memory=ProjectMemoryContext(
                summary="Python app with a conventional verification flow.",
                known_commands=["python -m py_compile main.py"],
                recent_context="Recent work focused on keeping the plan minimal.",
            ),
            plan=plan,
        )
        self.assertIsNotNone(summary)
        self.assertIn('"project_memory"', provider.calls[0]["user"])
        self.assertIn("python -m py_compile main.py", provider.calls[0]["user"])
        self.assertTrue(any(event.stage == "apply_project_context" for event in summary.events))


if __name__ == "__main__":
    unittest.main()
