from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from run_session import AxiomRunManager


class RunSessionPersistenceTests(unittest.TestCase):
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
                "task": "Modify demo.txt to contain: PERSISTED CONTENT",
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
        self,
        manager: AxiomRunManager,
        session_id: str,
        expected: set[str],
        timeout: float = 10.0,
    ) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            session = manager.get_run_state(session_id)
            if session["status"] in expected:
                return session
            time.sleep(0.05)
        self.fail(f"Timed out waiting for {expected}")

    def test_prepare_run_persists_and_reloads(self) -> None:
        session = self._prepare_modify()
        reloaded = AxiomRunManager()
        loaded = reloaded.get_run_state(session["id"])
        self.assertEqual(loaded["status"], "awaiting_plan_approval")
        self.assertEqual(loaded["task_interpretation"]["target_path"], "demo.txt")

    def test_awaiting_approval_run_survives_restart(self) -> None:
        session = self._prepare_modify()
        reloaded = AxiomRunManager()
        loaded = reloaded.get_run_state(session["id"])
        self.assertEqual(loaded["status"], "awaiting_plan_approval")
        self.assertIsNotNone(loaded["change_preview"])

    def test_completed_run_survives_restart(self) -> None:
        session = self._prepare_modify()
        self.manager.approve_plan(session["id"])
        session = self._wait_for_status(
            self.manager, session["id"], {"awaiting_phase_approval"}
        )
        self.manager.approve_phase(session["id"])
        self._wait_for_status(self.manager, session["id"], {"awaiting_phase_approval"})
        self.manager.approve_phase(session["id"])
        completed = self._wait_for_status(self.manager, session["id"], {"completed"})
        reloaded = AxiomRunManager()
        loaded = reloaded.get_run_state(completed["id"])
        self.assertEqual(loaded["status"], "completed")
        self.assertEqual(
            loaded["final_execution_result"]["message"], "Modified 'demo.txt'."
        )

    def test_cancelled_run_survives_restart(self) -> None:
        session = self._prepare_modify()
        cancelled = self.manager.cancel_run(session["id"], "Stop now")
        reloaded = AxiomRunManager()
        loaded = reloaded.get_run_state(cancelled["id"])
        self.assertEqual(loaded["status"], "cancelled")
        self.assertEqual(
            loaded["final_execution_result"]["details"]["reason"], "Stop now"
        )

    def test_active_run_reconciles_to_recovered_after_restart(self) -> None:
        session = self._prepare_modify()
        internal = self.manager._session(session["id"])
        internal["status"] = "running"
        internal["activity"].append("Simulated active execution before restart.")
        self.manager._persist_session(internal)

        reloaded = AxiomRunManager()
        loaded = reloaded.get_run_state(session["id"])
        self.assertEqual(loaded["status"], "recovered_after_restart")
        self.assertEqual(
            loaded["final_execution_result"]["details"]["prior_status"],
            "running",
        )
        self.assertTrue(
            any("Recovered after restart" in item for item in loaded["activity"])
        )

    def test_preview_provenance_survives_reload(self) -> None:
        session = self._prepare_modify()
        reloaded = AxiomRunManager()
        loaded = reloaded.get_run_state(session["id"])
        preview_change = loaded["change_preview"]["file_changes"][0]
        self.assertEqual(preview_change["origin"]["source"], "snapshot")
        self.assertIsNotNone(preview_change["content_hashes"]["after_sha256"])

    def test_decline_flow_survives_reload(self) -> None:
        session = self._prepare_modify()
        declined = self.manager.decline_plan(session["id"], "Need review")
        reloaded = AxiomRunManager()
        loaded = reloaded.get_run_state(declined["id"])
        self.assertEqual(loaded["status"], "declined_plan")
        self.assertEqual(
            loaded["final_execution_result"]["details"]["reason"], "Need review"
        )

    def test_prepare_run_rejects_invalid_auto_repair_attempts(self) -> None:
        payload = {
            "projectPath": str(self.project_root),
            "mode": "implement",
            "task": "Modify demo.txt to contain: PERSISTED CONTENT",
            "approvalMode": "phased",
            "verificationProfile": "basic",
            "verificationCommands": [],
            "commandPolicy": "permissive",
            "previewChanges": True,
            "buildRepoIndex": False,
            "autoRepair": True,
            "autoRepairAttempts": 0,
            "scopePaths": ["."],
            "protectedPaths": [],
        }
        with self.assertRaisesRegex(
            ValueError, "autoRepairAttempts must be an integer >= 1."
        ):
            self.manager.prepare_run(payload)


if __name__ == "__main__":
    unittest.main()
