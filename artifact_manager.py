from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from models import ArtifactReference


class ArtifactManager:
    ROOT_DIR_NAME = ".axiom"

    def __init__(self, project_root: str, run_id: str | None = None) -> None:
        self.project_root = Path(project_root).resolve()
        self.axiom_root = self.project_root / self.ROOT_DIR_NAME
        self.runs_root = self.axiom_root / "artifacts"
        self.run_id = run_id or f"{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}"
        self.run_root = self.runs_root / self.run_id
        self.run_root.mkdir(parents=True, exist_ok=True)

    def save_json(self, filename: str, payload: dict) -> ArtifactReference:
        path = self.run_root / filename
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        label = path.stem
        artifact_type = path.stem.split("_")[0]
        return ArtifactReference(
            artifact_type=artifact_type,
            label=label,
            path=str(path),
        )

    def build_filename(self, prefix: str) -> str:
        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        suffix = uuid4().hex[:6]
        return f"{prefix}_{timestamp}_{suffix}.json"

    def run_reference(self) -> ArtifactReference:
        return ArtifactReference(
            artifact_type="run",
            label=self.run_id,
            path=str(self.run_root),
        )
