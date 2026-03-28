from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from artifact_manager import ArtifactManager
from models import (
    ApprovalMode,
    CommandPolicyMode,
    Mode,
    PermissionSet,
    TaskAction,
    TaskInterpretation,
    VerificationConfig,
    VerificationProfile,
)
from orchestrator import Orchestrator
from preview_manager import PreviewManager
from scope_manager import ScopeManager
from snapshot_manager import SnapshotManager
from run_session import AxiomRunManager
from workspace_manager import WorkspaceManager


class PreviewProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temp_dir.name)
        self.permissions = PermissionSet(True, True, True)
        self.scope_manager = ScopeManager(str(self.project_root), ["."], [])
        self.workspace = WorkspaceManager(str(self.project_root), self.permissions, self.scope_manager)
        self.artifact_manager = ArtifactManager(str(self.project_root))
        self.preview_manager = PreviewManager(self.workspace, self.scope_manager, self.artifact_manager)
        self.snapshots = SnapshotManager(str(self.project_root))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_overwrite_existing_file_uses_snapshot_before_state(self) -> None:
        target = self.project_root / "demo.txt"
        target.write_text("ORIGINAL CONTENT", encoding="utf-8")
        snapshot = self.snapshots.create_snapshot()
        target.write_text("LIVE MUTATION AFTER SNAPSHOT", encoding="utf-8")

        preview = self.preview_manager.build_preview(
            TaskInterpretation(
                raw_task="modify demo.txt",
                summary="Modify demo.txt",
                action=TaskAction.MODIFY_FILE,
                target_path="demo.txt",
                content="UPDATED CONTENT",
            ),
            snapshot_reference=snapshot,
            plan_steps=["Inspect Existing File State"],
            persist=False,
        )

        assert preview is not None
        change = preview.file_changes[0]
        self.assertTrue(change.before_exists)
        self.assertEqual(change.before_preview, "ORIGINAL CONTENT")
        self.assertIn("-ORIGINAL CONTENT", change.diff_preview or "")
        self.assertEqual(change.origin["source"], "snapshot")
        self.assertEqual(change.origin["snapshot_id"], snapshot.snapshot_id)
        self.assertIsNotNone(change.content_hashes["before_sha256"])
        self.assertIsNotNone(change.content_hashes["after_sha256"])

    def test_create_new_file_uses_none_before_state(self) -> None:
        snapshot = self.snapshots.create_snapshot()
        preview = self.preview_manager.build_preview(
            TaskInterpretation(
                raw_task="create new.txt",
                summary="Create new.txt",
                action=TaskAction.CREATE_FILE,
                target_path="new.txt",
                content="HELLO",
            ),
            snapshot_reference=snapshot,
            plan_steps=["Apply Requested File Change"],
            persist=False,
        )

        assert preview is not None
        change = preview.file_changes[0]
        self.assertFalse(change.before_exists)
        self.assertIsNone(change.before_preview)
        self.assertEqual(change.action, "create")
        self.assertIsNone(change.content_hashes["before_sha256"])

    def test_multi_file_preview_uses_snapshot_for_all_entries(self) -> None:
        (self.project_root / "a.txt").write_text("A0", encoding="utf-8")
        (self.project_root / "b.txt").write_text("B0", encoding="utf-8")
        snapshot = self.snapshots.create_snapshot()
        (self.project_root / "a.txt").write_text("A-live", encoding="utf-8")
        (self.project_root / "b.txt").write_text("B-live", encoding="utf-8")

        preview = self.preview_manager.build_preview_for_writes(
            snapshot_reference=snapshot,
            writes=[
                {"path": "a.txt", "content": "A1"},
                {"path": "b.txt", "content": "B1"},
            ],
            plan_steps=["Phase preview"],
            persist=False,
        )

        self.assertEqual(len(preview.file_changes), 2)
        self.assertEqual(preview.file_changes[0].before_preview, "A0")
        self.assertEqual(preview.file_changes[1].before_preview, "B0")
        self.assertTrue(all(change.origin["snapshot_id"] == snapshot.snapshot_id for change in preview.file_changes))

    def test_phased_run_preview_is_frozen_before_approvals(self) -> None:
        (self.project_root / "phase.txt").write_text("PHASE BASELINE", encoding="utf-8")
        manager = AxiomRunManager()
        session = manager.prepare_run(
            {
                "projectPath": str(self.project_root),
                "mode": "implement",
                "task": "Modify phase.txt to contain: PHASE TARGET",
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
        (self.project_root / "phase.txt").write_text("MUTATED BEFORE APPROVAL", encoding="utf-8")

        preview = session["change_preview"]["file_changes"][0]
        self.assertEqual(preview["before_preview"], "PHASE BASELINE")
        self.assertEqual(session["status"], "awaiting_plan_approval")

class OrchestratorRollbackPreviewTest(unittest.TestCase):
    def test_failed_run_followed_by_rollback_restores_snapshot_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            (project_root / "rollback.txt").write_text("ROLLBACK BASELINE", encoding="utf-8")
            orchestrator = Orchestrator(str(project_root))

            import builtins

            original_input = builtins.input
            builtins.input = lambda prompt="": "y"
            try:
                result = orchestrator.run(
                    mode=Mode.IMPLEMENT,
                    task="Modify rollback.txt to contain: BROKEN RESULT",
                    scope_paths=["."],
                    protected_paths=[],
                    verification_config=VerificationConfig(
                        profile=VerificationProfile.COMMANDS_ONLY,
                        commands=['python -c "import sys; sys.exit(1)"'],
                    ),
                    build_repo_index=False,
                    command_policy=CommandPolicyMode.PERMISSIVE,
                    preview_changes=True,
                    auto_repair=False,
                    approval_mode=ApprovalMode.NORMAL,
                )
            finally:
                builtins.input = original_input

            self.assertFalse(result.final_execution_result.success)
            self.assertIsNotNone(result.change_preview)
            self.assertEqual(result.change_preview.file_changes[0].before_preview, "ROLLBACK BASELINE")
            SnapshotManager(str(project_root)).restore_snapshot(result.snapshot_reference.snapshot_id)
            self.assertEqual((project_root / "rollback.txt").read_text(encoding="utf-8"), "ROLLBACK BASELINE")


if __name__ == "__main__":
    unittest.main()
