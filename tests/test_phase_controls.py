from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from run_session import AxiomRunManager

TERMINAL_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "declined_plan",
    "declined_phase",
}


class PhaseControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temp_dir.name)
        (self.project_root / "demo.txt").write_text("BASELINE", encoding="utf-8")
        self.manager = AxiomRunManager()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _prepare_modify(self) -> dict:
        return self.manager.prepare_run(
            {
                "projectPath": str(self.project_root),
                "mode": "implement",
                "task": "Modify demo.txt to contain: NEXT CONTENT",
                "approvalMode": "phased",
                "verificationProfile": "basic",
                "verificationCommands": [],
                "commandPolicy": "permissive",
                "previewChanges": True,
                "buildRepoIndex": False,
                "autoRepair": False,
                "scopePaths": ["."],
                "protectedPaths": [],
            }
        )

    def _wait_for_status(
        self, session_id: str, expected: set[str], timeout: float = 5.0
    ) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            session = self.manager.get_run_state(session_id)
            if session["status"] in expected:
                return session
            time.sleep(0.05)
        self.fail(
            f"Timed out waiting for status {expected}. Last state: {self.manager.get_run_state(session_id)}"
        )

    def test_read_only_phase_auto_runs_after_plan_approval(self) -> None:
        session = self._prepare_modify()
        session = self.manager.approve_plan(session["id"])
        session = self._wait_for_status(session["id"], {"awaiting_phase_approval"})
        self.assertEqual(session["pending_phase"], "modify")
        self.assertEqual(session["step_results"][0]["phase"], "understand")
        self.assertEqual(session["step_results"][0]["status"], "completed")
        understand_policy = next(
            policy
            for policy in session["phase_policies"]
            if policy["phase"] == "understand"
        )
        self.assertEqual(understand_policy["classification"], "read_only")
        self.assertTrue(understand_policy["auto_ran"])

    def test_write_phase_still_requires_approval(self) -> None:
        session = self._prepare_modify()
        session = self.manager.approve_plan(session["id"])
        session = self._wait_for_status(session["id"], {"awaiting_phase_approval"})
        modify_policy = next(
            policy
            for policy in session["phase_policies"]
            if policy["phase"] == "modify"
        )
        self.assertEqual(modify_policy["classification"], "writes_files")
        self.assertTrue(modify_policy["approval_required"])
        self.assertEqual(session["pending_phase"], "modify")

    def test_decline_plan_stops_cleanly(self) -> None:
        session = self._prepare_modify()
        session = self.manager.decline_plan(session["id"], "Need to review")
        self.assertEqual(session["status"], "declined_plan")
        self.assertEqual(
            session["final_execution_result"]["details"]["reason"], "Need to review"
        )
        self.assertIsNone(session["snapshot_reference"])

    def test_decline_phase_stops_cleanly(self) -> None:
        session = self._prepare_modify()
        self.manager.approve_plan(session["id"])
        session = self._wait_for_status(session["id"], {"awaiting_phase_approval"})
        session = self.manager.decline_phase(session["id"], "Do not overwrite yet")
        self.assertEqual(session["status"], "declined_phase")
        self.assertIsNone(session["pending_phase"])
        skipped_phases = [
            step for step in session["step_results"] if step["status"] == "skipped"
        ]
        self.assertTrue(skipped_phases)

    def test_cancel_before_execution_stops_cleanly(self) -> None:
        session = self._prepare_modify()
        session = self.manager.cancel_run(session["id"], "Pause work")
        self.assertEqual(session["status"], "cancelled")
        self.assertEqual(
            session["final_execution_result"]["details"]["reason"], "Pause work"
        )

    def test_cancel_during_phased_flow_prevents_future_phases(self) -> None:
        session = self._prepare_modify()
        self.manager.approve_plan(session["id"])
        session = self._wait_for_status(session["id"], {"awaiting_phase_approval"})
        session = self.manager.cancel_run(session["id"], "Stop before modify")
        self.assertEqual(session["status"], "cancelled")
        self.assertEqual(
            (self.project_root / "demo.txt").read_text(encoding="utf-8"), "BASELINE"
        )

    def test_cancel_during_long_running_command_is_cooperative_and_terminal(
        self,
    ) -> None:
        session = self.manager.prepare_run(
            {
                "projectPath": str(self.project_root),
                "mode": "implement",
                "task": "Run command python -c \"import time; print('start'); time.sleep(5); print('end')\"",
                "approvalMode": "normal",
                "verificationProfile": "none",
                "verificationCommands": [],
                "commandPolicy": "permissive",
                "previewChanges": False,
                "buildRepoIndex": False,
                "autoRepair": False,
                "scopePaths": ["."],
                "protectedPaths": [],
            }
        )
        self.manager.approve_plan(session["id"])
        time.sleep(0.25)
        session = self.manager.cancel_run(session["id"], "Interrupt active command")
        self.assertIn(session["status"], {"cancelling", "cancelled"})
        session = self._wait_for_status(session["id"], {"cancelled"}, timeout=10.0)
        self.assertEqual(session["status"], "cancelled")
        self.assertTrue(session["commands_run"])
        self.assertTrue(session["commands_run"][-1]["cancelled"])

    def test_normal_phased_run_still_succeeds(self) -> None:
        session = self._prepare_modify()
        self.manager.approve_plan(session["id"])
        session = self._wait_for_status(session["id"], {"awaiting_phase_approval"})
        session = self.manager.approve_phase(session["id"])
        session = self._wait_for_status(session["id"], {"awaiting_phase_approval"})
        self.assertEqual(session["pending_phase"], "verify")
        self.manager.approve_phase(session["id"])
        session = self._wait_for_status(session["id"], {"completed"})
        self.assertEqual(
            (self.project_root / "demo.txt").read_text(encoding="utf-8"), "NEXT CONTENT"
        )


if __name__ == "__main__":
    unittest.main()
