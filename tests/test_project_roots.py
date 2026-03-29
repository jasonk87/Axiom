from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from project_manager import ProjectManager
from run_session import AxiomRunManager


class ProjectRootTests(unittest.TestCase):
    def test_project_can_be_created_from_folder_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            manager = ProjectManager(Path(temp_dir) / "state")
            project = manager.create_project(str(workspace), name="Workspace")
            self.assertEqual(project["name"], "Workspace")
            self.assertEqual(project["root_path"], str(workspace.resolve()))
            self.assertTrue(project["id"])

    def test_reopening_same_root_does_not_duplicate_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            manager = ProjectManager(Path(temp_dir) / "state")
            first = manager.create_project(str(workspace), name="Workspace")
            second = manager.create_project(str(workspace), name="Workspace Again")
            self.assertEqual(first["id"], second["id"])
            self.assertEqual(len(manager.list_projects()), 1)

    def test_invalid_project_path_is_rejected_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = ProjectManager(Path(temp_dir) / "state")
            missing = Path(temp_dir) / "missing"
            with self.assertRaisesRegex(ValueError, "Project path does not exist"):
                manager.create_project(str(missing))

    def test_existing_sessions_are_migrated_into_projects(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_root = Path(temp_dir) / "state"
            project_root = Path(temp_dir) / "legacy_project"
            project_root.mkdir()
            sessions_root = project_root / ".axiom" / "sessions"
            sessions_root.mkdir(parents=True)
            session_path = sessions_root / "legacy-session.json"
            session_path.write_text(
                json.dumps(
                    {
                        "id": "legacy-session",
                        "project_root": str(project_root.resolve()),
                        "status": "completed",
                        "mode": "plan",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "updated_at": "2026-01-01T00:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            registry_path = state_root / "session_registry.json"
            state_root.mkdir(parents=True)
            registry_path.write_text(
                json.dumps(
                    {
                        "legacy-session": {
                            "id": "legacy-session",
                            "project_root": str(project_root.resolve()),
                            "path": str(session_path),
                            "status": "completed",
                            "updated_at": "2026-01-01T00:00:00+00:00",
                            "created_at": "2026-01-01T00:00:00+00:00",
                            "artifact_run_id": None,
                            "mode": "plan",
                        }
                    }
                ),
                encoding="utf-8",
            )

            manager = AxiomRunManager(state_root=state_root)
            runs = manager.list_runs()
            self.assertEqual(len(runs), 1)
            self.assertIn("project_id", runs[0])
            projects = manager.list_projects()["projects"]
            self.assertEqual(len(projects), 1)
            self.assertEqual(projects[0]["root_path"], str(project_root.resolve()))
            detail = manager.get_project(projects[0]["id"])
            self.assertEqual(detail["memory"]["project_id"], projects[0]["id"])
            self.assertIn("project_summary", detail["memory"])

    def test_sessions_and_runs_are_tied_to_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_root = Path(temp_dir) / "state"
            project_root = Path(temp_dir) / "project"
            project_root.mkdir()
            (project_root / "demo.txt").write_text("hello", encoding="utf-8")

            manager = AxiomRunManager(state_root=state_root)
            project = manager.create_project(str(project_root), name="Project")
            session = manager.prepare_run(
                {
                    "projectId": project["id"],
                    "mode": "plan",
                    "task": "Plan a safe update",
                    "approvalMode": "normal",
                    "verificationProfile": "none",
                    "verificationCommands": [],
                    "commandPolicy": "permissive",
                    "previewChanges": False,
                    "buildRepoIndex": False,
                    "autoRepair": False,
                    "scopePaths": ["demo.txt"],
                    "protectedPaths": [],
                }
            )
            self.assertEqual(session["project_id"], project["id"])
            self.assertEqual(session["project_root"], str(project_root.resolve()))
            self.assertEqual(session["session_id"], session["id"])
            self.assertTrue(session["run_id"])
            self.assertEqual(
                session["result"]["effective_scope"]["paths"], ["demo.txt"]
            )

    def test_prepare_run_requires_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = AxiomRunManager(state_root=Path(temp_dir) / "state")
            with self.assertRaisesRegex(ValueError, "project must be selected"):
                manager.prepare_run(
                    {
                        "mode": "plan",
                        "task": "Plan a safe update",
                        "approvalMode": "normal",
                        "verificationProfile": "none",
                        "verificationCommands": [],
                        "commandPolicy": "permissive",
                        "previewChanges": False,
                        "buildRepoIndex": False,
                        "autoRepair": False,
                        "scopePaths": [],
                        "protectedPaths": [],
                    }
                )

    def test_global_and_project_memory_persist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_root = Path(temp_dir) / "state"
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            manager = AxiomRunManager(state_root=state_root)
            project = manager.create_project(str(workspace), name="Workspace")
            manager.update_llm_settings({"enabled": True, "model": "llama3.2:1b"})

            reloaded = AxiomRunManager(state_root=state_root)
            detail = reloaded.get_project(project["id"])
            self.assertEqual(detail["memory"]["project_id"], project["id"])
            memory_path = state_root / "memory.json"
            payload = json.loads(memory_path.read_text(encoding="utf-8"))
            self.assertEqual(
                payload["global_memory"]["model_preference"]["model"], "llama3.2:1b"
            )
            self.assertIn(project["id"], payload["project_memories"])


if __name__ == "__main__":
    unittest.main()
