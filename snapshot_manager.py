from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from models import SnapshotReference


class SnapshotManager:
    SNAPSHOT_DIR_NAME = ".axiom_snapshots"

    def __init__(self, project_root: str) -> None:
        self.project_root = Path(project_root).resolve()
        self.snapshot_root = self.project_root / self.SNAPSHOT_DIR_NAME

    def create_snapshot(self) -> SnapshotReference:
        self.snapshot_root.mkdir(exist_ok=True)
        snapshot_id = f"{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}"
        destination = self.snapshot_root / snapshot_id / "workspace"
        shutil.copytree(
            self.project_root,
            destination,
            ignore=shutil.ignore_patterns(self.SNAPSHOT_DIR_NAME),
        )
        metadata_path = destination.parent / "metadata.json"
        metadata_path.write_text(
            json.dumps(
                {
                    "snapshot_id": snapshot_id,
                    "created_at_utc": datetime.utcnow().isoformat(),
                    "source_project_root": str(self.project_root),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return SnapshotReference(snapshot_id=snapshot_id, snapshot_path=str(destination.parent))

    def restore_snapshot(self, snapshot_id: str | None = None) -> SnapshotReference:
        reference = self.latest_snapshot() if snapshot_id is None else self._reference_for(snapshot_id)
        snapshot_workspace = Path(reference.snapshot_path) / "workspace"

        for item in self.project_root.iterdir():
            if item.name == self.SNAPSHOT_DIR_NAME:
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()

        for item in snapshot_workspace.iterdir():
            destination = self.project_root / item.name
            if item.is_dir():
                shutil.copytree(item, destination)
            else:
                shutil.copy2(item, destination)

        return reference

    def latest_snapshot(self) -> SnapshotReference:
        candidates = sorted(
            [path for path in self.snapshot_root.iterdir() if path.is_dir()],
            key=lambda path: path.name,
        )
        if not candidates:
            raise FileNotFoundError("No snapshots are available to restore.")
        latest = candidates[-1]
        return SnapshotReference(snapshot_id=latest.name, snapshot_path=str(latest))

    def _reference_for(self, snapshot_id: str) -> SnapshotReference:
        path = self.snapshot_root / snapshot_id
        if not path.exists():
            raise FileNotFoundError(f"Snapshot '{snapshot_id}' was not found.")
        return SnapshotReference(snapshot_id=snapshot_id, snapshot_path=str(path))

    @staticmethod
    def workspace_path(reference: SnapshotReference) -> Path:
        return Path(reference.snapshot_path) / "workspace"

    def resolve_snapshot_path(self, reference: SnapshotReference, relative_path: str) -> Path:
        return (self.workspace_path(reference) / relative_path).resolve()
