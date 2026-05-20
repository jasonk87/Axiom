from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from llm_client import GeminiProvider, LLMSettings


class _FakeHTTPResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class GeminiProviderTests(unittest.TestCase):
    def _settings(self) -> LLMSettings:
        return LLMSettings(
            enabled=True,
            review_enabled=True,
            provider="gemini",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            model="gemini-2.5-flash-lite",
            timeout_seconds=5,
            retry_limit=2,
            temperature=0.1,
            compression_enabled=True,
            compression_threshold=5,
            embedding_model="gemini-embedding-001",
            api_key="test-key",
        )

    def test_generate_extracts_candidate_text(self) -> None:
        provider = GeminiProvider(self._settings())
        payload = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "hello"}, {"text": " world"}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"totalTokenCount": 12},
        }

        with patch("llm_client.request.urlopen", return_value=_FakeHTTPResponse(payload)) as opened:
            response = provider.generate("system", "user")

        self.assertEqual(response.raw_text, "hello world")
        self.assertEqual(response.provider, "gemini")
        request = opened.call_args.args[0]
        self.assertIn("models/gemini-2.5-flash-lite:generateContent", request.full_url)
        self.assertIn("key=test-key", request.full_url)

    def test_embed_extracts_values(self) -> None:
        provider = GeminiProvider(self._settings())
        payload = {"embedding": {"values": [0.1, 0.2, 0.3]}}

        with patch("llm_client.request.urlopen", return_value=_FakeHTTPResponse(payload)):
            self.assertEqual(provider.embed("text"), [0.1, 0.2, 0.3])


if __name__ == "__main__":
    unittest.main()
