from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from fuelintel.eia import fetch_us_retail_prices


class _Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


class EiaTests(unittest.TestCase):
    def test_missing_key_never_falls_back_to_invented_data(self):
        self.assertEqual(fetch_us_retail_prices(""), {})

    def test_official_series_are_validated_and_sorted(self):
        payload = {
            "response": {
                "data": [
                    {"series": "EMM_EPMR_PTE_NUS_DPG", "period": "2026-09-14", "value": "4.319"},
                    {"series": "EMM_EPMR_PTE_NUS_DPG", "period": "2026-09-07", "value": "4.157"},
                    {"series": "EMD_EPD2D_PTE_NUS_DPG", "period": "2026-09-14", "value": "6.285"},
                    {"series": "EMD_EPD2D_PTE_NUS_DPG", "period": "bad", "value": "999"},
                ]
            }
        }
        with patch("urllib.request.urlopen", return_value=_Response(payload)):
            rows = fetch_us_retail_prices("test-key")
        self.assertEqual(rows["Gasolina regular"][-1], ("2026-09-14", 4.319))
        self.assertEqual(rows["Diésel"][-1], ("2026-09-14", 6.285))
        self.assertNotIn("Gasolina premium", rows)


if __name__ == "__main__":
    unittest.main()
