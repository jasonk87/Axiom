from __future__ import annotations

from pathlib import Path

from models import BlockedAction, PermissionSet
from permission_manager import PermissionManager
from scope_manager import ScopeManager, ScopeViolationError


class WorkspaceManager:
    def __init__(
        self,
        project_root: str,
        permissions: PermissionSet,
        scope_manager: ScopeManager,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.permissions = permissions
        self.scope_manager = scope_manager
        self.files_read: list[str] = []
        self.files_modified: list[str] = []
        self.blocked_actions: list[BlockedAction] = []

    def resolve_path(self, relative_path: str) -> Path:
        candidate = (self.project_root / relative_path).resolve()
        if self.project_root not in candidate.parents and candidate != self.project_root:
            raise ValueError(f"Path '{relative_path}' escapes the project workspace.")
        return candidate

    def read_text(self, relative_path: str) -> str:
        PermissionManager.require_read(self.permissions)
        target = self.resolve_path(relative_path)
        self._enforce_read(target)
        content = target.read_text(encoding="utf-8")
        self._track_once(self.files_read, str(target))
        return content

    def write_text(self, relative_path: str, content: str) -> Path:
        PermissionManager.require_write(self.permissions)
        target = self.resolve_path(relative_path)
        self._enforce_write(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self._track_once(self.files_modified, str(target))
        return target

    def exists(self, relative_path: str) -> bool:
        PermissionManager.require_read(self.permissions)
        target = self.resolve_path(relative_path)
        self._enforce_read(target)
        return target.exists()

    def list_files(self) -> list[str]:
        PermissionManager.require_read(self.permissions)
        files: list[str] = []
        for path in self.project_root.rglob("*"):
            if path.is_file() and ".axiom_snapshots" not in path.parts and ".axiom" not in path.parts:
                try:
                    self._enforce_read(path)
                except ScopeViolationError:
                    continue
                files.append(str(path))
        return sorted(files)

    @staticmethod
    def _track_once(bucket: list[str], value: str) -> None:
        if value not in bucket:
            bucket.append(value)

    def track_read_path(self, path: Path) -> None:
        self._track_once(self.files_read, str(path))

    def _enforce_read(self, target: Path) -> None:
        try:
            self.scope_manager.enforce_read(target)
        except ScopeViolationError as error:
            self.record_blocked_action(error.to_blocked_action())
            raise

    def _enforce_write(self, target: Path) -> None:
        try:
            self.scope_manager.enforce_write(target)
        except ScopeViolationError as error:
            self.record_blocked_action(error.to_blocked_action())
            raise

    def record_blocked_action(self, blocked_action: BlockedAction) -> None:
        if not any(
            existing.action == blocked_action.action
            and existing.path == blocked_action.path
            and existing.reason == blocked_action.reason
            for existing in self.blocked_actions
        ):
            self.blocked_actions.append(blocked_action)
