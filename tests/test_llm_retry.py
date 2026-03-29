from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from artifact_manager import ArtifactManager
from llm_client import LLMProviderError, LLMResponse, LLMSettings
from llm_plan_service import LocalLLMPlanService
from llm_retry import StructuredOutputRetryEngine
from models import ProjectMemoryContext, RepoIndexSummary, TaskAction, TaskInterpretation, VerificationConfig, VerificationProfile
from scope_manager import ScopeManager


VALID_PLAN_JSON = """
{
  "steps": [
    {
      "id": "step-1",
      "type": "discovery",
      "title": "Inspect files",
      "description": "Find the relevant files first.",
      "dependencies": [],
      "scope_hint": "src",
      "expected_outcome": "Relevant files are identified.",
      "phase": "understand",
      "risk_hint": "low",
      "approval_hint": "included_in_run_approval"
    }
  ]
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
        return LLMResponse(
            raw_text=response,
            provider="fake",
            model="fake-model",
            metadata={},
        )


class LLMRetryTests(unittest.TestCase):
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
            embedding_model="nomic-embed-text",
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

    def test_valid_json_accepted_first_try(self) -> None:
        engine, _ = self._engine([VALID_PLAN_JSON])
        result = engine.generate_plan_output("system", "user", "fallback")
        self.assertIsNotNone(result.payload)
        self.assertTrue(result.summary.accepted)
        self.assertEqual(result.summary.attempts_used, 1)

    def test_malformed_json_retried_then_accepted(self) -> None:
        engine, _ = self._engine(["not-json", VALID_PLAN_JSON])
        result = engine.generate_plan_output("system", "user", "fallback")
        self.assertIsNotNone(result.payload)
        self.assertTrue(result.summary.accepted)
        self.assertEqual(result.summary.attempts_used, 2)
        self.assertTrue(any(event.stage == "retry_output" for event in result.summary.events))

    def test_wrong_schema_retried_then_accepted(self) -> None:
        wrong_schema = '{"steps":[{"id":"step-1"}]}'
        engine, _ = self._engine([wrong_schema, VALID_PLAN_JSON])
        result = engine.generate_plan_output("system", "user", "fallback")
        self.assertIsNotNone(result.payload)
        self.assertTrue(result.summary.accepted)
        self.assertTrue(any(event.stage == "validate_response" and event.status == "failed" for event in result.summary.events))

    def test_retry_exhaustion_falls_back_cleanly(self) -> None:
        engine, _ = self._engine(["{}", "{}", "{}"])
        result = engine.generate_plan_output("system", "user", "fallback")
        self.assertIsNone(result.payload)
        self.assertFalse(result.summary.accepted)
        self.assertTrue(result.summary.fallback_used)
        self.assertIn("exhausted", result.summary.final_message.lower())

    def test_provider_unavailable_falls_back_cleanly(self) -> None:
        error = LLMProviderError("provider_unavailable", "Ollama is unavailable.")
        engine, _ = self._engine([error])
        result = engine.generate_plan_output("system", "user", "fallback")
        self.assertIsNone(result.payload)
        self.assertFalse(result.summary.accepted)
        self.assertTrue(result.summary.fallback_used)
        self.assertTrue(any(event.stage == "query_model" and event.status == "failed" for event in result.summary.events))

    def test_project_memory_is_included_in_plan_input_and_can_change_output(self) -> None:
        def response_for(user_instruction: str) -> str:
            if "npm run build" in user_instruction:
                return """
                {
                  "steps": [
                    {
                      "id": "step-1",
                      "type": "discovery",
                      "title": "Inspect project command usage",
                      "description": "Confirm the existing build command fits the task.",
                      "dependencies": [],
                      "scope_hint": ".",
                      "expected_outcome": "Known command is confirmed.",
                      "phase": "understand",
                      "risk_hint": "low",
                      "approval_hint": "included_in_run_approval"
                    }
                  ]
                }
                """.strip()
            return VALID_PLAN_JSON

        class MemoryAwareProvider:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def generate(self, system_instruction: str, user_instruction: str) -> LLMResponse:
                self.calls.append(user_instruction)
                return LLMResponse(raw_text=response_for(user_instruction), provider="fake", model="fake-model", metadata={})

        provider = MemoryAwareProvider()
        service = LocalLLMPlanService(settings=self.settings, provider=provider)
        interpretation = TaskInterpretation(
            raw_task="Plan a safe update",
            summary="Plan a safe update",
            action=TaskAction.RUN_COMMAND,
            command="npm run build",
        )
        scope_manager = ScopeManager(str(self.project_root), [], [])

        plan_without_memory, summary_without_memory = service.generate_plan(
            artifact_manager=self.artifact_manager,
            interpretation=interpretation,
            scope_manager=scope_manager,
            verification=VerificationConfig(profile=VerificationProfile.NONE, commands=[]),
            repo_index_summary=RepoIndexSummary(generated=False),
            project_memory=None,
        )
        plan_with_memory, summary_with_memory = service.generate_plan(
            artifact_manager=self.artifact_manager,
            interpretation=interpretation,
            scope_manager=scope_manager,
            verification=VerificationConfig(profile=VerificationProfile.NONE, commands=[]),
            repo_index_summary=RepoIndexSummary(generated=False),
            project_memory=ProjectMemoryContext(
                summary="Node project with a standard npm build flow.",
                known_commands=["npm run build"],
                recent_context="Recent tasks used the build command successfully.",
            ),
        )

        self.assertIsNotNone(plan_without_memory)
        self.assertIsNotNone(plan_with_memory)
        assert plan_without_memory is not None
        assert plan_with_memory is not None
        self.assertNotEqual(plan_without_memory.to_dict(), plan_with_memory.to_dict())
        self.assertIn('"project_memory"', provider.calls[0])
        self.assertIn('"known_commands": []', provider.calls[0])
        self.assertIn('"known_commands": [', provider.calls[1])
        self.assertIn("npm run build", provider.calls[1])
        assert summary_with_memory is not None
        self.assertTrue(any(event.stage == "apply_project_context" for event in summary_with_memory.events))


if __name__ == "__main__":
    unittest.main()
