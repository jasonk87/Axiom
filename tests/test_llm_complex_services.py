import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from artifact_manager import ArtifactManager
from llm_client import LLMResponse, LLMSettings
from llm_decompose_service import LocalLLMDecomposeService
from llm_implement_service import LocalLLMImplementService
from llm_replan_service import LocalLLMReplanService
from models import (
    SubTask,
    TaskAction,
    TaskInterpretation,
    VerificationConfig,
    VerificationProfile,
)
from scope_manager import ScopeManager


class StubProvider:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def generate(self, system_instruction: str, user_instruction: str) -> LLMResponse:
        self.calls.append((system_instruction, user_instruction))
        if not self.responses:
            raise RuntimeError("StubProvider ran out of mock responses.")
        return LLMResponse(
            raw_text=self.responses.pop(0),
            provider="stub",
            model="stub-model",
            metadata={"done": True},
        )


class LLMServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.project_root = Path(self.temp_dir.name)
        self.artifact_manager = ArtifactManager(str(self.project_root))
        self.settings = LLMSettings.from_dict({"enabled": True, "retry_limit": 1})
        self.scope_manager = ScopeManager(str(self.project_root))
        self.verification = VerificationConfig(profile=VerificationProfile.NONE)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_decompose_service_success(self) -> None:
        provider = StubProvider(
            [
                json.dumps(
                    {
                        "subtasks": [
                            {
                                "action": "create_file",
                                "description": "Create a new entry point",
                                "target_path": "main.py",
                                "command": None,
                            },
                            {
                                "action": "run_command",
                                "description": "Run tests",
                                "target_path": None,
                                "command": "pytest",
                            },
                        ]
                    }
                )
            ]
        )
        service = LocalLLMDecomposeService(self.settings, provider)
        interpretation = TaskInterpretation(
            raw_task="Make a complex change",
            summary="Make a complex change",
            action=TaskAction.COMPLEX,
        )

        subtasks, summary = service.generate_decomposition(
            artifact_manager=self.artifact_manager,
            interpretation=interpretation,
            scope_manager=self.scope_manager,
            verification=self.verification,
            repo_index_summary=None,
            project_memory=None,
        )

        self.assertIsNotNone(subtasks)
        assert subtasks is not None
        self.assertEqual(len(subtasks), 2)
        self.assertEqual(subtasks[0].action, "create_file")
        self.assertEqual(subtasks[1].command, "pytest")
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertTrue(summary.accepted)

    def test_implement_service_success(self) -> None:
        provider = StubProvider([json.dumps({"content": "print('hello world')"})])
        service = LocalLLMImplementService(self.settings, provider)
        subtask = SubTask(
            action="create_file",
            description="Create hello world file",
            target_path="hello.py",
        )

        content, summary = service.generate_implementation(
            artifact_manager=self.artifact_manager,
            subtask=subtask,
            scope_manager=self.scope_manager,
            repo_index_summary=None,
            project_memory=None,
        )

        self.assertEqual(content, "print('hello world')")
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertTrue(summary.accepted)

    def test_replan_service_success(self) -> None:
        provider = StubProvider(
            [
                json.dumps(
                    {
                        "is_complete": False,
                        "reasoning": "Need to run a format check.",
                        "new_subtasks": [
                            {
                                "action": "run_command",
                                "description": "Format the code",
                                "target_path": None,
                                "command": "black .",
                            }
                        ],
                    }
                )
            ]
        )
        service = LocalLLMReplanService(self.settings, provider)
        interpretation = TaskInterpretation(
            raw_task="Make a complex change",
            summary="Make a complex change",
            action=TaskAction.COMPLEX,
        )
        completed_subtasks = [
            SubTask(
                action="create_file", description="Done step", result_summary="Success"
            )
        ]

        payload, summary = service.generate_replan(
            artifact_manager=self.artifact_manager,
            interpretation=interpretation,
            completed_subtasks=completed_subtasks,
            scope_manager=self.scope_manager,
            verification=self.verification,
            repo_index_summary=None,
            project_memory=None,
        )

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertFalse(payload["is_complete"])
        self.assertEqual(len(payload["new_subtasks"]), 1)
        self.assertEqual(payload["new_subtasks"][0]["command"], "black .")
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertTrue(summary.accepted)


if __name__ == "__main__":
    unittest.main()
