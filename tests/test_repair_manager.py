from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from models import (
    CommandResult,
    FailureCategory,
    FailureClassification,
    TaskAction,
    TaskInterpretation,
    VerificationConfig,
    VerificationProfile,
)
from repair_manager import RepairManager
from scope_manager import ScopeManager


class FakeTerminal:
    def __init__(self, outcomes: list[bool]) -> None:
        self._outcomes = list(outcomes)

    def run(self, command: str) -> CommandResult:
        success = self._outcomes.pop(0) if self._outcomes else False
        return CommandResult(
            command=command,
            exit_code=0 if success else 1,
            stdout="ok" if success else "",
            stderr="" if success else "failed",
            success=success,
            summary="ok" if success else "failed",
        )


class FakeVerificationManager:
    def run_commands(self):
        return []


class FakeWorkspace:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str]] = []

    def write_text(self, target_path: str, content: str) -> None:
        self.writes.append((target_path, content))


class FakeFileVerificationManager:
    def __init__(self, outcomes: list[bool]) -> None:
        self._outcomes = list(outcomes)

    def verify_file_write(self, target_path: str, content: str) -> dict[str, object]:
        success = self._outcomes.pop(0) if self._outcomes else False
        return {
            "path": target_path,
            "exists": True,
            "content_matches": success,
            "configured_commands_passed": True,
        }


class RepairManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temp_dir.name)
        self.scope_manager = ScopeManager(str(self.project_root), [], [])
        self.manager = RepairManager()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _command_failure(self) -> FailureClassification:
        return FailureClassification(
            category=FailureCategory.COMMAND_EXECUTION_FAILURE,
            reason="command failed",
            source="execution",
        )

    def _command_interpretation(self) -> TaskInterpretation:
        return TaskInterpretation(
            raw_task="run echo",
            summary="run echo",
            action=TaskAction.RUN_COMMAND,
            command="echo hi",
        )

    def _verification_failure(self) -> FailureClassification:
        return FailureClassification(
            category=FailureCategory.VERIFICATION_FAILURE,
            reason="verification mismatch",
            source="verification",
        )

    def _file_interpretation(self) -> TaskInterpretation:
        return TaskInterpretation(
            raw_task="update file",
            summary="update file",
            action=TaskAction.MODIFY_FILE,
            target_path="src/example.py",
            content="print('ok')\n",
        )

    def test_command_repair_succeeds_within_bounded_attempts(self) -> None:
        repair_summary = self.manager.build_repair_summary(
            auto_repair_enabled=True,
            failure=self._command_failure(),
            interpretation=self._command_interpretation(),
            verification=VerificationConfig(profile=VerificationProfile.NONE),
            repo_index_summary=None,
            scope_manager=self.scope_manager,
            repair_attempt_limit=3,
        )

        repaired = self.manager.attempt_repair(
            repair_summary=repair_summary,
            failure=self._command_failure(),
            interpretation=self._command_interpretation(),
            verification=VerificationConfig(profile=VerificationProfile.NONE),
            workspace=None,  # not used in command repair branch
            terminal=FakeTerminal([False, True]),
            verification_manager=FakeVerificationManager(),
            max_attempts=3,
        )

        self.assertTrue(repaired.attempted)
        self.assertEqual(repaired.repair_attempt_limit, 3)
        self.assertEqual(repaired.repair_attempts_used, 2)
        self.assertIsNotNone(repaired.repair_execution_result)
        self.assertTrue(repaired.repair_execution_result.success)

    def test_command_repair_fails_after_exhausting_attempts(self) -> None:
        repair_summary = self.manager.build_repair_summary(
            auto_repair_enabled=True,
            failure=self._command_failure(),
            interpretation=self._command_interpretation(),
            verification=VerificationConfig(profile=VerificationProfile.NONE),
            repo_index_summary=None,
            scope_manager=self.scope_manager,
            repair_attempt_limit=2,
        )

        repaired = self.manager.attempt_repair(
            repair_summary=repair_summary,
            failure=self._command_failure(),
            interpretation=self._command_interpretation(),
            verification=VerificationConfig(profile=VerificationProfile.NONE),
            workspace=None,  # not used in command repair branch
            terminal=FakeTerminal([False, False]),
            verification_manager=FakeVerificationManager(),
            max_attempts=2,
        )

        self.assertTrue(repaired.attempted)
        self.assertEqual(repaired.repair_attempts_used, 2)
        self.assertIsNotNone(repaired.repair_execution_result)
        self.assertFalse(repaired.repair_execution_result.success)
        self.assertEqual(repaired.repair_execution_result.details["repair_attempt"], 2)
        self.assertEqual(
            repaired.repair_execution_result.details["repair_attempt_limit"], 2
        )

    def test_attempts_are_clamped_to_configured_repair_limit(self) -> None:
        repair_summary = self.manager.build_repair_summary(
            auto_repair_enabled=True,
            failure=self._command_failure(),
            interpretation=self._command_interpretation(),
            verification=VerificationConfig(profile=VerificationProfile.NONE),
            repo_index_summary=None,
            scope_manager=self.scope_manager,
            repair_attempt_limit=2,
        )

        repaired = self.manager.attempt_repair(
            repair_summary=repair_summary,
            failure=self._command_failure(),
            interpretation=self._command_interpretation(),
            verification=VerificationConfig(profile=VerificationProfile.NONE),
            workspace=None,  # not used in command repair branch
            terminal=FakeTerminal([False, False, True]),
            verification_manager=FakeVerificationManager(),
            max_attempts=5,
        )

        self.assertFalse(repaired.repair_execution_result.success)
        self.assertEqual(repaired.repair_attempts_used, 2)
        self.assertEqual(repaired.repair_attempt_limit, 2)

    def test_verification_repair_retries_until_success(self) -> None:
        repair_summary = self.manager.build_repair_summary(
            auto_repair_enabled=True,
            failure=self._verification_failure(),
            interpretation=self._file_interpretation(),
            verification=VerificationConfig(profile=VerificationProfile.BASIC),
            repo_index_summary=None,
            scope_manager=self.scope_manager,
            repair_attempt_limit=3,
        )

        repaired = self.manager.attempt_repair(
            repair_summary=repair_summary,
            failure=self._verification_failure(),
            interpretation=self._file_interpretation(),
            verification=VerificationConfig(profile=VerificationProfile.BASIC),
            workspace=FakeWorkspace(),
            terminal=FakeTerminal([]),
            verification_manager=FakeFileVerificationManager([False, True]),
            max_attempts=3,
        )

        self.assertTrue(repaired.repair_execution_result.success)
        self.assertEqual(repaired.repair_attempts_used, 2)
        self.assertIn("rewrite_and_reverify_target", repaired.repair_strategies_tried)

    def test_verification_repair_fails_after_bound(self) -> None:
        repair_summary = self.manager.build_repair_summary(
            auto_repair_enabled=True,
            failure=self._verification_failure(),
            interpretation=self._file_interpretation(),
            verification=VerificationConfig(profile=VerificationProfile.BASIC),
            repo_index_summary=None,
            scope_manager=self.scope_manager,
            repair_attempt_limit=2,
        )

        repaired = self.manager.attempt_repair(
            repair_summary=repair_summary,
            failure=self._verification_failure(),
            interpretation=self._file_interpretation(),
            verification=VerificationConfig(profile=VerificationProfile.BASIC),
            workspace=FakeWorkspace(),
            terminal=FakeTerminal([]),
            verification_manager=FakeFileVerificationManager([False, False]),
            max_attempts=2,
        )

        self.assertFalse(repaired.repair_execution_result.success)
        self.assertEqual(repaired.repair_attempts_used, 2)
        self.assertEqual(repaired.repair_execution_result.details["repair_attempt"], 2)


if __name__ == "__main__":
    unittest.main()
