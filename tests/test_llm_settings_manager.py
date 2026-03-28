from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from llm_settings_manager import LLMSettingsManager


class LLMSettingsManagerTests(unittest.TestCase):
    def test_settings_persist_across_manager_reload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = LLMSettingsManager(root)
            updated = manager.update_settings(
                {
                    "enabled": True,
                    "review_enabled": False,
                    "provider": "ollama",
                    "base_url": "http://127.0.0.1:11434",
                    "model": "phi4-mini",
                    "retry_limit": 3,
                }
            )
            self.assertTrue(updated.enabled)
            self.assertEqual(updated.model, "phi4-mini")

            reloaded = LLMSettingsManager(root).get_settings()
            self.assertTrue(reloaded.enabled)
            self.assertFalse(reloaded.review_enabled)
            self.assertEqual(reloaded.provider, "ollama")
            self.assertEqual(reloaded.model, "phi4-mini")
            self.assertEqual(reloaded.retry_limit, 3)


if __name__ == "__main__":
    unittest.main()
