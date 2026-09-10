from __future__ import annotations

import unittest
from unittest.mock import patch

from fuelintel.ai import DeepSeekEditor


class DeepSeekTests(unittest.TestCase):
    def test_is_disabled_without_secret(self):
        editor = DeepSeekEditor("", "deepseek-v4-flash")
        self.assertFalse(editor.enabled)
        self.assertIsNone(editor.review("evidence", "safe draft"))

    @patch("fuelintel.ai.urllib.request.urlopen")
    def test_timeout_falls_back_without_blocking(self, mocked):
        mocked.side_effect = TimeoutError()
        editor = DeepSeekEditor("secret", "deepseek-v4-flash")
        self.assertIsNone(editor.review("evidence", "safe draft"))


if __name__ == "__main__":
    unittest.main()

