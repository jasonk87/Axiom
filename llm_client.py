from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib import error, request
from urllib.parse import quote

DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "llama3.2:3b"
DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash-lite"
DEFAULT_GEMINI_EMBEDDING_MODEL = "gemini-embedding-001"


class LLMProviderError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class LLMSettings:
    enabled: bool
    review_enabled: bool
    provider: str
    base_url: str
    model: str
    timeout_seconds: int
    retry_limit: int
    temperature: float
    compression_enabled: bool
    compression_threshold: int
    embedding_model: str
    api_key: str = ""

    @classmethod
    def from_env(cls) -> "LLMSettings":
        enabled_raw = os.getenv("AXIOM_LLM_ENABLED", "false").strip().lower()
        enabled = enabled_raw in {"1", "true", "yes", "on"}
        review_raw = os.getenv("AXIOM_LLM_REVIEW_ENABLED", "true").strip().lower()
        review_enabled = review_raw in {"1", "true", "yes", "on"}
        compression_raw = (
            os.getenv("AXIOM_LLM_COMPRESSION_ENABLED", "true").strip().lower()
        )
        compression_enabled = compression_raw in {"1", "true", "yes", "on"}
        provider = os.getenv("AXIOM_LLM_PROVIDER", "gemini").strip().lower()
        default_base_url = (
            DEFAULT_GEMINI_BASE_URL if provider == "gemini" else DEFAULT_OLLAMA_BASE_URL
        )
        default_model = (
            DEFAULT_GEMINI_MODEL if provider == "gemini" else DEFAULT_OLLAMA_MODEL
        )
        default_embedding_model = (
            DEFAULT_GEMINI_EMBEDDING_MODEL
            if provider == "gemini"
            else "nomic-embed-text"
        )
        return cls(
            enabled=enabled,
            review_enabled=review_enabled,
            provider=provider,
            base_url=os.getenv("AXIOM_LLM_BASE_URL", default_base_url).strip(),
            model=os.getenv("AXIOM_LLM_MODEL", default_model).strip(),
            timeout_seconds=int(os.getenv("AXIOM_LLM_TIMEOUT_SECONDS", "20")),
            retry_limit=max(0, int(os.getenv("AXIOM_LLM_RETRY_LIMIT", "2"))),
            temperature=float(os.getenv("AXIOM_LLM_TEMPERATURE", "0.1")),
            compression_enabled=compression_enabled,
            compression_threshold=max(
                1, int(os.getenv("AXIOM_LLM_COMPRESSION_THRESHOLD", "5"))
            ),
            embedding_model=os.getenv(
                "AXIOM_LLM_EMBEDDING_MODEL", default_embedding_model
            ).strip(),
            api_key=os.getenv(
                "AXIOM_LLM_API_KEY",
                os.getenv("AXIOM_GEMINI_API_KEY", os.getenv("GEMINI_API_KEY", "")),
            ).strip(),
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "LLMSettings":
        defaults = cls.from_env()
        return cls(
            enabled=bool(payload.get("enabled", defaults.enabled)),
            review_enabled=bool(payload.get("review_enabled", defaults.review_enabled)),
            provider=str(payload.get("provider", defaults.provider)).strip().lower(),
            base_url=str(payload.get("base_url", defaults.base_url)).strip(),
            model=str(payload.get("model", defaults.model)).strip(),
            timeout_seconds=max(
                1, int(payload.get("timeout_seconds", defaults.timeout_seconds))
            ),
            retry_limit=max(0, int(payload.get("retry_limit", defaults.retry_limit))),
            temperature=float(payload.get("temperature", defaults.temperature)),
            compression_enabled=bool(
                payload.get("compression_enabled", defaults.compression_enabled)
            ),
            compression_threshold=max(
                1,
                int(
                    payload.get("compression_threshold", defaults.compression_threshold)
                ),
            ),
            embedding_model=str(
                payload.get("embedding_model", defaults.embedding_model)
            ).strip(),
            api_key=str(payload.get("api_key", defaults.api_key)).strip(),
        )

    def to_dict(self, *, include_secrets: bool = False) -> dict[str, Any]:
        payload = {
            "enabled": self.enabled,
            "review_enabled": self.review_enabled,
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
            "retry_limit": self.retry_limit,
            "temperature": self.temperature,
            "compression_enabled": self.compression_enabled,
            "compression_threshold": self.compression_threshold,
            "embedding_model": self.embedding_model,
            "api_key_configured": bool(self.api_key),
        }
        if include_secrets:
            payload["api_key"] = self.api_key
        return payload


@dataclass
class LLMResponse:
    raw_text: str
    provider: str
    model: str
    metadata: dict[str, Any]


class OllamaProvider:
    def __init__(self, settings: LLMSettings) -> None:
        self.settings = settings

    def generate(
        self,
        system_instruction: str,
        user_instruction: str,
    ) -> LLMResponse:
        payload = {
            "model": self.settings.model,
            "prompt": user_instruction,
            "system": system_instruction,
            "stream": False,
            "options": {
                "temperature": self.settings.temperature,
            },
        }
        body = json.dumps(payload).encode("utf-8")
        url = f"{self.settings.base_url.rstrip('/')}/api/generate"
        http_request = request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(
                http_request, timeout=self.settings.timeout_seconds
            ) as response:
                data = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as caught:
            raise LLMProviderError(
                "ollama_http_error", f"Ollama returned HTTP {caught.code}."
            ) from caught
        except error.URLError as caught:
            raise LLMProviderError(
                "provider_unavailable",
                f"Ollama is unavailable at {self.settings.base_url}.",
            ) from caught
        except TimeoutError as caught:
            raise LLMProviderError(
                "timeout", "Timed out waiting for the local model response."
            ) from caught

        text = str(data.get("response", "")).strip()
        if not text:
            raise LLMProviderError(
                "empty_response", "The local model returned an empty response."
            )

        return LLMResponse(
            raw_text=text,
            provider="ollama",
            model=self.settings.model,
            metadata={
                "done": data.get("done"),
                "total_duration": data.get("total_duration"),
                "prompt_eval_count": data.get("prompt_eval_count"),
                "eval_count": data.get("eval_count"),
            },
        )

    def embed(self, text: str) -> list[float]:
        payload = {
            "model": self.settings.embedding_model,
            "prompt": text,
        }
        body = json.dumps(payload).encode("utf-8")
        url = f"{self.settings.base_url.rstrip('/')}/api/embeddings"
        http_request = request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(
                http_request, timeout=self.settings.timeout_seconds
            ) as response:
                data = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as caught:
            raise LLMProviderError(
                "ollama_http_error",
                f"Ollama returned HTTP {caught.code} during embedding.",
            ) from caught
        except error.URLError as caught:
            raise LLMProviderError(
                "provider_unavailable",
                f"Ollama is unavailable at {self.settings.base_url}.",
            ) from caught
        except TimeoutError as caught:
            raise LLMProviderError(
                "timeout", "Timed out waiting for the local model embedding response."
            ) from caught

        embedding = data.get("embedding")
        if not isinstance(embedding, list) or not embedding:
            raise LLMProviderError(
                "invalid_embedding",
                "The local model returned an invalid embedding response.",
            )

        return embedding


class GeminiProvider:
    def __init__(self, settings: LLMSettings) -> None:
        self.settings = settings

    def _api_key(self) -> str:
        if not self.settings.api_key:
            raise LLMProviderError(
                "missing_api_key", "Gemini requires an API key before requests can run."
            )
        return self.settings.api_key

    def _request_json(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        url = (
            f"{self.settings.base_url.rstrip('/')}/{endpoint}"
            f"?key={quote(self._api_key(), safe='')}"
        )
        http_request = request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(
                http_request, timeout=self.settings.timeout_seconds
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as caught:
            detail = caught.read().decode("utf-8", errors="replace")
            raise LLMProviderError(
                "gemini_http_error",
                f"Gemini returned HTTP {caught.code}: {detail[:240]}",
            ) from caught
        except error.URLError as caught:
            raise LLMProviderError(
                "provider_unavailable",
                f"Gemini is unavailable at {self.settings.base_url}.",
            ) from caught
        except TimeoutError as caught:
            raise LLMProviderError(
                "timeout", "Timed out waiting for the Gemini response."
            ) from caught

    def generate(
        self,
        system_instruction: str,
        user_instruction: str,
    ) -> LLMResponse:
        model = self.settings.model.removeprefix("models/")
        payload = {
            "system_instruction": {"parts": [{"text": system_instruction}]},
            "contents": [{"role": "user", "parts": [{"text": user_instruction}]}],
            "generationConfig": {
                "temperature": self.settings.temperature,
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        data = self._request_json(f"models/{quote(model, safe='')}:generateContent", payload)
        candidates = data.get("candidates")
        parts = []
        if isinstance(candidates, list) and candidates:
            content = candidates[0].get("content", {})
            raw_parts = content.get("parts", [])
            if isinstance(raw_parts, list):
                parts = [str(part.get("text", "")) for part in raw_parts if isinstance(part, dict)]
        text = "".join(parts).strip()
        if not text:
            raise LLMProviderError("empty_response", "Gemini returned an empty response.")

        return LLMResponse(
            raw_text=text,
            provider="gemini",
            model=self.settings.model,
            metadata={
                "finish_reason": candidates[0].get("finishReason") if candidates else None,
                "usage_metadata": data.get("usageMetadata"),
            },
        )

    def embed(self, text: str) -> list[float]:
        model = self.settings.embedding_model.removeprefix("models/")
        payload = {
            "model": f"models/{model}",
            "content": {"parts": [{"text": text}]},
            "taskType": "RETRIEVAL_DOCUMENT",
        }
        data = self._request_json(f"models/{quote(model, safe='')}:embedContent", payload)
        values = data.get("embedding", {}).get("values")
        if not isinstance(values, list) or not values:
            raise LLMProviderError(
                "invalid_embedding", "Gemini returned an invalid embedding response."
            )
        return [float(value) for value in values]


def build_provider(settings: LLMSettings):
    if settings.provider == "ollama":
        return OllamaProvider(settings)
    if settings.provider == "gemini":
        return GeminiProvider(settings)
    raise LLMProviderError(
        "unsupported_provider", f"Unsupported provider '{settings.provider}'."
    )
