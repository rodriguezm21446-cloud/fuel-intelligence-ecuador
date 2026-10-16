from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from fuelintel.eia import (
    fetch_ecuador_crude_production,
    fetch_us_retail_prices,
    fetch_us_weekly_inventory,
)


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

    def test_ecuador_crude_production_is_validated_and_sorted(self):
        payload = {
            "response": {
                "data": [
                    {"period": "2026-06", "value": "460"},
                    {"period": "2026-05", "value": "459"},
                    {"period": "2026-04", "value": "99999"},
                    {"period": "bad", "value": "12"},
                ]
            }
        }
        with patch("urllib.request.urlopen", return_value=_Response(payload)):
            rows = fetch_ecuador_crude_production("test-key")
        self.assertEqual(rows, [("2026-05", 459.0), ("2026-06", 460.0)])

    def test_ecuador_crude_production_without_key_is_empty(self):
        self.assertEqual(fetch_ecuador_crude_production(""), [])

    def test_us_weekly_inventory_is_validated_and_sorted(self):
        payload = {
            "response": {
                "data": [
                    {"series": "WCRSTUS1", "period": "2026-09-11", "value": "858500"},
                    {"series": "WCRSTUS1", "period": "2026-09-04", "value": "854700"},
                    {"series": "WGTSTUS1", "period": "2026-09-11", "value": "249500"},
                    {"series": "WCRSTUS1", "period": "2026-09-01", "value": "1"},
                    {"series": "DESCONOCIDA", "period": "2026-09-11", "value": "500000"},
                ]
            }
        }
        with patch("urllib.request.urlopen", return_value=_Response(payload)):
            rows = fetch_us_weekly_inventory("test-key")
        self.assertEqual(rows["Crudo"], [("2026-09-04", 854700.0), ("2026-09-11", 858500.0)])
        self.assertEqual(rows["Gasolina"], [("2026-09-11", 249500.0)])
        self.assertNotIn("Diésel", rows)

    def test_us_weekly_inventory_without_key_is_empty(self):
        self.assertEqual(fetch_us_weekly_inventory(""), {})


if __name__ == "__main__":
    unittest.main()
