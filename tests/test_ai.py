from __future__ import annotations

import unittest
from unittest.mock import patch

from fuelintel.ai import EDITORIAL_GUARDRAILS, GeminiEditor


class GeminiTests(unittest.TestCase):
    def test_guardrail_rejects_automatic_brent_causality(self):
        self.assertIn("no demuestra por sí sola", EDITORIAL_GUARDRAILS)
        self.assertIn("documento oficial", EDITORIAL_GUARDRAILS)

    def test_is_disabled_without_secret(self):
        editor = GeminiEditor("", "gemini-3.5-flash-lite")
        self.assertFalse(editor.enabled)
        self.assertIsNone(editor.review("evidence", "safe draft"))

    @patch("fuelintel.ai.urllib.request.urlopen")
    def test_timeout_falls_back_without_blocking(self, mocked):
        mocked.side_effect = TimeoutError()
        editor = GeminiEditor("secret", "gemini-3.5-flash-lite")
        self.assertIsNone(editor.review("evidence", "safe draft"))


if __name__ == "__main__":
    unittest.main()

