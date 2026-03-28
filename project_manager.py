from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
import time
from uuid import uuid4


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ProjectRecord:
    id: str
    name: str
    root_path: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GlobalMemoryRecord:
    model_preference: dict
    ui_preferences: dict

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProjectMemoryRecord:
    project_id: str
    project_summary: str
    known_commands: list[str]
    recent_context_summary: str

    def to_dict(self) -> dict:
        return asdict(self)


class ProjectManager:
    def __init__(self, state_root: Path) -> None:
        self.state_root = state_root
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.projects_path = self.state_root / "projects.json"
        self.context_path = self.state_root / "project_context.json"
        self.memory_path = self.state_root / "memory.json"
        self.lock = Lock()
        self.projects = self._load_projects()
        self.context = self._load_context()
        self.memory = self._load_memory()
        self._ensure_memory_defaults()
        if self.projects and self.context.get("active_project_id") not in self.projects:
            self.context["active_project_id"] = next(iter(self.projects))
            self.context["active_session_id"] = None
            self._persist_context()

    def _load_projects(self) -> dict[str, ProjectRecord]:
        if not self.projects_path.exists():
            return {}
        payload = json.loads(self.projects_path.read_text(encoding="utf-8"))
        return {
            item["id"]: ProjectRecord(
                id=item["id"],
                name=item["name"],
                root_path=item["root_path"],
                created_at=item["created_at"],
                updated_at=item["updated_at"],
            )
            for item in payload.get("projects", [])
        }

    def _load_context(self) -> dict:
        if not self.context_path.exists():
            return {"active_project_id": None, "active_session_id": None}
        payload = json.loads(self.context_path.read_text(encoding="utf-8"))
        return {
            "active_project_id": payload.get("active_project_id"),
            "active_session_id": payload.get("active_session_id"),
        }

    def _load_memory(self) -> dict:
        if not self.memory_path.exists():
            return {
                "global_memory": GlobalMemoryRecord(
                    model_preference={},
                    ui_preferences={},
                ).to_dict(),
                "project_memories": {},
            }
        payload = json.loads(self.memory_path.read_text(encoding="utf-8"))
        return {
            "global_memory": payload.get(
                "global_memory",
                GlobalMemoryRecord(model_preference={}, ui_preferences={}).to_dict(),
            ),
            "project_memories": payload.get("project_memories", {}),
        }

    def _atomic_write(self, path: Path, payload: dict) -> None:
        temp_path = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
        temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        for attempt in range(5):
            try:
                temp_path.replace(path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05)

    def _persist_projects(self) -> None:
        self._atomic_write(
            self.projects_path,
            {"projects": [project.to_dict() for project in sorted(self.projects.values(), key=lambda item: item.updated_at, reverse=True)]},
        )

    def _persist_context(self) -> None:
        self._atomic_write(self.context_path, self.context)

    def _persist_memory(self) -> None:
        self._atomic_write(self.memory_path, self.memory)

    def _default_project_memory(self, project_id: str) -> dict:
        return ProjectMemoryRecord(
            project_id=project_id,
            project_summary="",
            known_commands=[],
            recent_context_summary="",
        ).to_dict()

    def _ensure_memory_defaults(self) -> None:
        changed = False
        global_memory = self.memory.setdefault("global_memory", {})
        if "model_preference" not in global_memory:
            global_memory["model_preference"] = {}
            changed = True
        if "ui_preferences" not in global_memory:
            global_memory["ui_preferences"] = {}
            changed = True
        project_memories = self.memory.setdefault("project_memories", {})
        for project_id in self.projects:
            if project_id not in project_memories:
                project_memories[project_id] = self._default_project_memory(project_id)
                changed = True
        stale_ids = [project_id for project_id in project_memories if project_id not in self.projects]
        for stale_id in stale_ids:
            project_memories.pop(stale_id, None)
            changed = True
        if changed:
            self._persist_memory()

    @staticmethod
    def normalize_root(root_path: str) -> str:
        resolved = Path(root_path).expanduser().resolve()
        if not resolved.exists():
            raise ValueError(f"Project path does not exist: {resolved}")
        if not resolved.is_dir():
            raise ValueError(f"Project path must be a folder: {resolved}")
        return str(resolved)

    @staticmethod
    def default_name_for(root_path: str) -> str:
        name = Path(root_path).name
        return name or root_path

    def list_projects(self) -> list[dict]:
        with self.lock:
            return [
                {
                    **project.to_dict(),
                    "memory": json.loads(
                        json.dumps(
                            self.memory["project_memories"].get(project.id, self._default_project_memory(project.id))
                        )
                    ),
                }
                for project in sorted(self.projects.values(), key=lambda item: item.updated_at, reverse=True)
            ]

    def get_context(self) -> dict:
        with self.lock:
            return dict(self.context)

    def get_global_memory(self) -> dict:
        with self.lock:
            return json.loads(json.dumps(self.memory["global_memory"]))

    def update_global_memory(self, *, model_preference: dict | None = None, ui_preferences: dict | None = None) -> dict:
        with self.lock:
            if model_preference is not None:
                self.memory["global_memory"]["model_preference"] = dict(model_preference)
            if ui_preferences is not None:
                self.memory["global_memory"]["ui_preferences"] = dict(ui_preferences)
            self._persist_memory()
            return json.loads(json.dumps(self.memory["global_memory"]))

    def set_active(self, project_id: str | None, session_id: str | None = None) -> None:
        with self.lock:
            self.context["active_project_id"] = project_id
            self.context["active_session_id"] = session_id
            self._persist_context()

    def get_project(self, project_id: str) -> ProjectRecord:
        with self.lock:
            project = self.projects.get(project_id)
            if project is None:
                raise KeyError(f"Unknown project '{project_id}'.")
            return project

    def get_project_detail(self, project_id: str) -> dict:
        with self.lock:
            project = self.projects.get(project_id)
            if project is None:
                raise KeyError(f"Unknown project '{project_id}'.")
            return {
                **project.to_dict(),
                "memory": json.loads(
                    json.dumps(
                        self.memory["project_memories"].get(project_id, self._default_project_memory(project_id))
                    )
                ),
            }

    def find_by_root(self, root_path: str) -> ProjectRecord | None:
        normalized = self.normalize_root(root_path)
        with self.lock:
            for project in self.projects.values():
                if project.root_path == normalized:
                    return project
        return None

    def create_project(self, root_path: str, name: str | None = None) -> dict:
        normalized = self.normalize_root(root_path)
        with self.lock:
            existing = next((project for project in self.projects.values() if project.root_path == normalized), None)
            if existing is not None:
                existing.updated_at = _utc_now()
                if name:
                    existing.name = name
                self._persist_projects()
                self.memory["project_memories"].setdefault(existing.id, self._default_project_memory(existing.id))
                self._persist_memory()
                self.context["active_project_id"] = existing.id
                self.context["active_session_id"] = None
                self._persist_context()
                return existing.to_dict()

            now = _utc_now()
            project = ProjectRecord(
                id=uuid4().hex,
                name=(name or self.default_name_for(normalized)).strip(),
                root_path=normalized,
                created_at=now,
                updated_at=now,
            )
            self.projects[project.id] = project
            self._persist_projects()
            self.memory["project_memories"][project.id] = self._default_project_memory(project.id)
            self._persist_memory()
            self.context["active_project_id"] = project.id
            self.context["active_session_id"] = None
            self._persist_context()
            return project.to_dict()

    def ensure_project_for_root(self, root_path: str, name: str | None = None) -> dict:
        existing = self.find_by_root(root_path)
        if existing is not None:
            return existing.to_dict()
        return self.create_project(root_path, name=name)

    def create_recovered_project(self, missing_root: str) -> dict:
        recovered_root = (self.state_root.parent / "recovered_projects" / uuid4().hex[:8]).resolve()
        recovered_root.mkdir(parents=True, exist_ok=True)
        return self.create_project(str(recovered_root), name=f"Recovered {Path(missing_root).name or 'project'}")

    def migrate_session_roots(self, sessions: list[dict]) -> bool:
        changed = False
        for session in sessions:
            root_path = session.get("project_root")
            if not root_path:
                continue
            try:
                project = self.ensure_project_for_root(root_path)
            except ValueError:
                project = self.create_recovered_project(root_path)
                session.setdefault("legacy_project_root", root_path)
                session["project_root"] = project["root_path"]
                changed = True
            if session.get("project_id") != project["id"]:
                session["project_id"] = project["id"]
                changed = True
            session.setdefault("project_name", project["name"])
        self._ensure_memory_defaults()
        if changed and self.context.get("active_project_id") not in self.projects:
            self.context["active_project_id"] = next(iter(self.projects), None)
            self._persist_context()
        return changed
