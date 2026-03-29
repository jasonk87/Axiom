from __future__ import annotations

import hashlib
from difflib import unified_diff

from artifact_manager import ArtifactManager
from models import (
    ChangePreview,
    PreviewFileChange,
    SnapshotReference,
    TaskAction,
    TaskInterpretation,
)
from scope_manager import ScopeManager, ScopeViolationError
from snapshot_manager import SnapshotManager
from workspace_manager import WorkspaceManager


class PreviewManager:
    PREVIEW_LIMIT = 400

    def __init__(
        self,
        workspace: WorkspaceManager,
        scope_manager: ScopeManager,
        artifact_manager: ArtifactManager,
    ) -> None:
        self.workspace = workspace
        self.scope_manager = scope_manager
        self.artifact_manager = artifact_manager

    def build_preview(
        self,
        interpretation: TaskInterpretation,
        snapshot_reference: SnapshotReference,
        plan_steps: list[str] | None = None,
        persist: bool = True,
    ) -> ChangePreview | None:
        preview = ChangePreview(
            intended_file_writes=[],
            intended_commands=[],
            relevant_steps=plan_steps or [],
        )

        if (
            interpretation.action in {TaskAction.CREATE_FILE, TaskAction.MODIFY_FILE}
            and interpretation.target_path is not None
        ):
            preview.intended_file_writes.append(interpretation.target_path)
            preview.file_changes.append(
                self._preview_file_write(
                    snapshot_reference,
                    interpretation.target_path,
                    interpretation.content or "",
                )
            )
        elif (
            interpretation.action == TaskAction.RUN_COMMAND
            and interpretation.command is not None
        ):
            preview.intended_commands.append(interpretation.command)
        elif interpretation.action == TaskAction.RESTORE_SNAPSHOT:
            preview.intended_file_writes.append("<whole workspace restore>")

        if (
            not preview.intended_file_writes
            and not preview.intended_commands
            and not preview.relevant_steps
        ):
            return None

        if persist:
            return self.persist_preview(preview)
        return preview

    def persist_preview(self, preview: ChangePreview) -> ChangePreview:
        artifact = self.artifact_manager.save_json(
            self.artifact_manager.build_filename("preview"),
            preview.to_dict(),
        )
        preview.artifact_reference = artifact
        return preview

    def build_preview_for_writes(
        self,
        snapshot_reference: SnapshotReference,
        writes: list[dict[str, str]],
        plan_steps: list[str] | None = None,
        persist: bool = True,
    ) -> ChangePreview:
        preview = ChangePreview(
            intended_file_writes=[item["path"] for item in writes],
            intended_commands=[],
            relevant_steps=plan_steps or [],
        )
        for write in writes:
            preview.file_changes.append(
                self._preview_file_write(
                    snapshot_reference, write["path"], write["content"]
                )
            )
        if persist:
            return self.persist_preview(preview)
        return preview

    def _preview_file_write(
        self,
        snapshot_reference: SnapshotReference,
        relative_path: str,
        new_content: str,
    ) -> PreviewFileChange:
        target = self.workspace.resolve_path(relative_path)
        blocked = False
        blocked_reason = ""
        try:
            self.scope_manager.enforce_write(target)
        except ScopeViolationError as error:
            blocked = True
            blocked_reason = error.reason

        snapshot_manager = SnapshotManager(str(self.workspace.project_root))
        snapshot_target = snapshot_manager.resolve_snapshot_path(
            snapshot_reference, relative_path
        )
        before_exists = snapshot_target.exists()
        before_content: str | None = None
        if before_exists:
            try:
                before_content = snapshot_target.read_text(encoding="utf-8")
                self.workspace.track_read_path(snapshot_target)
            except OSError:
                before_content = ""

        diff_text = "".join(
            unified_diff(
                (before_content or "").splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=f"{relative_path} (before)",
                tofile=f"{relative_path} (after)",
                lineterm="",
            )
        )
        action = "create" if not before_exists else "write"
        summary = (
            blocked_reason
            if blocked
            else f"{action.capitalize()} preview prepared for '{relative_path}'."
        )
        return PreviewFileChange(
            path=relative_path,
            action=action,
            blocked=blocked,
            summary=summary,
            before_exists=before_exists,
            before_preview=self._truncate(before_content) if before_exists else None,
            after_preview=self._truncate(new_content),
            diff_preview=self._truncate(diff_text),
            origin={
                "source": "snapshot",
                "snapshot_id": snapshot_reference.snapshot_id,
                "snapshot_path": snapshot_reference.snapshot_path,
                "relative_path": relative_path,
            },
            content_hashes={
                "before_sha256": (
                    self._hash_content(before_content) if before_exists else None
                ),
                "after_sha256": self._hash_content(new_content),
            },
        )

    def _truncate(self, text: str | None) -> str | None:
        if text is None:
            return None
        if text == "":
            return ""
        if len(text) <= self.PREVIEW_LIMIT:
            return text
        return f"{text[: self.PREVIEW_LIMIT]}... [truncated]"

    @staticmethod
    def _hash_content(text: str | None) -> str | None:
        if text is None:
            return None
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
