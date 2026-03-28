from __future__ import annotations

import json
from typing import Any

from artifact_manager import ArtifactManager
from llm_client import LLMSettings
from llm_retry import StructuredOutputRetryEngine
from models import (
    LLMActivityEvent,
    LLMStructuredResult,
    SubTask,
    TaskInterpretation,
)

class LocalLLMCompressService:
    def __init__(self, settings: LLMSettings | None = None, provider=None) -> None:
        self.settings = settings or LLMSettings.from_env()
        self.provider = provider

    def enabled(self) -> bool:
        return self.settings.enabled and self.settings.compression_enabled

    def generate_compression(
        self,
        artifact_manager: ArtifactManager,
        interpretation: TaskInterpretation,
        subtasks_to_compress: list[SubTask],
        existing_compressed_history: str | None,
    ) -> tuple[str | None, LLMStructuredResult | None]:
        if not self.enabled():
            return None, None

        engine = StructuredOutputRetryEngine(self.settings, artifact_manager, provider=self.provider)
        fallback_message = "Axiom failed to compress the subtask history."
        system_instruction = (
            "You are summarizing the progress of a complex coding task.\n"
            "Return only JSON.\n"
            "The top-level object must be {\"summary\": \"...\"}.\n"
            "Combine the existing history (if any) with the newly completed subtasks into a single, dense paragraph.\n"
            "Focus on what was actually accomplished, files modified, and critical outcomes."
        )

        structured_context = {
            "task": {
                "summary": interpretation.summary,
            },
            "existing_history": existing_compressed_history,
            "newly_completed_subtasks": [st.to_dict() for st in subtasks_to_compress],
            "instruction": "Generate a concise, dense paragraph summarizing all progress so far. Do not invent details.",
        }
        context_artifact = artifact_manager.save_json(
            artifact_manager.build_filename("llm_compress_context"),
            structured_context,
        )
        user_instruction = json.dumps(structured_context, indent=2, sort_keys=True)
        context_events = [
            LLMActivityEvent(
                stage="prepare_compression",
                status="completed",
                summary="Prepared subtask history for compression.",
                artifact_reference=context_artifact,
            )
        ]
        result = engine.generate_compress_output(
            system_instruction,
            user_instruction,
            fallback_message,
            initial_events=context_events,
            initial_artifacts=[context_artifact],
        )
        if result.payload is None:
            return None, result.summary

        return result.payload["summary"], result.summary
