from __future__ import annotations

from pathlib import Path

from models import BlockedAction


class ScopeViolationError(RuntimeError):
    def __init__(self, action: str, path: str, reason: str) -> None:
        super().__init__(reason)
        self.action = action
        self.path = path
        self.reason = reason

    def to_blocked_action(self) -> BlockedAction:
        return BlockedAction(action=self.action, path=self.path, reason=self.reason)


class ProtectedPathError(ScopeViolationError):
    pass


class ScopeManager:
    def __init__(
        self,
        project_root: str,
        scope_paths: list[str] | None = None,
        protected_paths: list[str] | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.scope_paths = self._normalize_entries(scope_paths or [])
        self.protected_paths = self._normalize_entries(protected_paths or [])

    def describe_effective_scope(self) -> dict[str, object]:
        if not self.scope_paths:
            return {
                "mode": "whole_project",
                "paths": [],
                "description": "No explicit scope provided. The full project workspace is in scope.",
            }
        return {
            "mode": "selected_paths",
            "paths": [self._relative_display(path) for path in self.scope_paths],
            "description": "Only the selected files or folders are in scope for reads and writes.",
        }

    def protected_path_labels(self) -> list[str]:
        return [self._relative_display(path) for path in self.protected_paths]

    def enforce_read(self, target: Path) -> None:
        if self.scope_paths and not self._matches_any(target, self.scope_paths):
            relative = self._relative_display(target)
            raise ScopeViolationError(
                action="read",
                path=relative,
                reason=f"Read blocked for '{relative}' because it is outside the active scope.",
            )

    def enforce_write(self, target: Path) -> None:
        relative = self._relative_display(target)
        if self._matches_any(target, self.protected_paths):
            raise ProtectedPathError(
                action="write",
                path=relative,
                reason=f"Write blocked for protected path '{relative}'.",
            )
        if self.scope_paths and not self._matches_any(target, self.scope_paths):
            raise ScopeViolationError(
                action="write",
                path=relative,
                reason=f"Write blocked for '{relative}' because it is outside the active scope.",
            )

    def target_scope_hint(self, relative_path: str | None) -> str:
        if relative_path:
            return relative_path
        if not self.scope_paths:
            return "whole project"
        return ", ".join(self._relative_display(path) for path in self.scope_paths)

    def enforce_full_workspace_write(self, action: str) -> None:
        if self.protected_paths:
            labels = ", ".join(self.protected_path_labels())
            raise ProtectedPathError(
                action=action,
                path=".",
                reason=(
                    f"{action.capitalize()} blocked because restoring the full workspace could modify "
                    f"protected paths: {labels}."
                ),
            )
        if self.scope_paths:
            labels = ", ".join(self._relative_display(path) for path in self.scope_paths)
            raise ScopeViolationError(
                action=action,
                path=".",
                reason=(
                    f"{action.capitalize()} blocked because restoring the full workspace would exceed "
                    f"the active scope: {labels}."
                ),
            )

    def _normalize_entries(self, entries: list[str]) -> list[Path]:
        normalized: list[Path] = []
        for raw_entry in entries:
            entry = raw_entry.strip()
            if not entry:
                continue
            candidate = (self.project_root / entry).resolve()
            if self.project_root not in candidate.parents and candidate != self.project_root:
                raise ValueError(f"Path '{entry}' escapes the project workspace.")
            normalized.append(candidate)
        return normalized

    @staticmethod
    def _matches_any(target: Path, candidates: list[Path]) -> bool:
        for candidate in candidates:
            if target == candidate or candidate in target.parents:
                return True
        return False

    def _relative_display(self, path: Path) -> str:
        if path == self.project_root:
            return "."
        return str(path.relative_to(self.project_root)).replace("\\", "/")
