from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

from llm_client import LLMProviderError, LLMSettings


class LLMSettingsManager:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or (Path(__file__).resolve().parent / ".axiom" / "state")
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "llm_settings.json"
        self.lock = Lock()
        self._cached = self._load()

    def _load(self) -> LLMSettings:
        if not self.path.exists():
            return LLMSettings.from_env()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return LLMSettings.from_dict(payload)

    def _atomic_write(self, payload: dict) -> None:
        temp_path = self.path.with_name(f"{self.path.name}.tmp")
        temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temp_path.replace(self.path)

    def get_settings(self) -> LLMSettings:
        with self.lock:
            return LLMSettings.from_dict(self._cached.to_dict())

    def update_settings(self, updates: dict) -> LLMSettings:
        with self.lock:
            merged = self._cached.to_dict()
            for key in [
                "enabled",
                "review_enabled",
                "provider",
                "base_url",
                "model",
                "timeout_seconds",
                "retry_limit",
                "temperature",
            ]:
                if key in updates:
                    merged[key] = updates[key]
            candidate = LLMSettings.from_dict(merged)
            if candidate.provider != "ollama":
                raise LLMProviderError(
                    "unsupported_provider",
                    f"Unsupported local provider '{candidate.provider}'.",
                )
            if not candidate.model:
                raise ValueError("Model name must not be empty.")
            if not candidate.base_url:
                raise ValueError("Base URL must not be empty.")
            self._cached = candidate
            self._atomic_write(candidate.to_dict())
            return LLMSettings.from_dict(candidate.to_dict())
