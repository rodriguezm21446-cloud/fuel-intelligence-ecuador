from __future__ import annotations

import unittest

from fuelintel.market_math import (
    DataIntegrityError,
    percentage_change,
    validate_chart_payload,
    validate_market_series,
)


class MarketMathTests(unittest.TestCase):
    def test_percentage_change_uses_decimal_arithmetic(self):
        self.assertEqual(percentage_change(2.55, 2.50), 2.0)

    def test_series_rejects_duplicate_or_non_finite_data(self):
        with self.assertRaises(DataIntegrityError):
            validate_market_series("WTI", [("2026-09-20", 90.0), ("2026-09-20", 91.0)])
        with self.assertRaises(DataIntegrityError):
            validate_market_series("WTI", [("2026-09-20", float("nan"))])

    def test_chart_rejects_series_that_does_not_match_axis(self):
        payload = {
            "title": "Prueba",
            "labels": ["A", "B"],
            "datasets": [{"label": "WTI", "values": [90.0]}],
            "references": [],
            "annotations": [],
            "displayDecimals": 2,
        }
        with self.assertRaises(DataIntegrityError):
            validate_chart_payload(payload)

    def test_historical_ranges_must_cover_every_series(self):
        payload = {
            "title": "Prueba",
            "labels": ["A"],
            "datasets": [
                {"label": "WTI", "values": [90.0]},
                {"label": "Brent", "values": [95.0]},
            ],
            "references": [{"label": "WTI", "min": 80.0, "max": 100.0}],
            "annotations": [],
            "displayDecimals": 2,
        }
        with self.assertRaises(DataIntegrityError):
            validate_chart_payload(payload)

    def test_price_type_requires_subtitle_statement(self):
        payload = {
            "title": "Prueba",
            "subtitle": "Vigencia 12/10–11/11 · USD por galón",
            "priceType": "TERMINAL",
            "labels": ["A"],
            "datasets": [{"label": "Extra", "color": "#000", "values": [2.9]}],
            "displayDecimals": 3,
        }
        with self.assertRaises(DataIntegrityError):
            validate_chart_payload(payload)
        payload["subtitle"] = "Vigencia 12/10–11/11 · precio en terminal · USD por galón"
        validate_chart_payload(payload)

    def test_price_type_mixing_is_rejected(self):
        payload = {
            "title": "Prueba",
            "subtitle": "Precio al público · USD por galón",
            "priceType": "PUBLICO",
            "labels": ["A"],
            "datasets": [
                {"label": "Extra", "color": "#000", "values": [3.17]},
                {"label": "Terminal", "color": "#111", "values": [2.95], "priceType": "TERMINAL"},
            ],
            "displayDecimals": 3,
        }
        with self.assertRaises(DataIntegrityError):
            validate_chart_payload(payload)

    def test_unknown_price_type_is_rejected(self):
        payload = {
            "title": "Prueba",
            "subtitle": "Whatever",
            "priceType": "SOCIAL",
            "labels": ["A"],
            "datasets": [{"label": "Extra", "color": "#000", "values": [3.17]}],
            "displayDecimals": 3,
        }
        with self.assertRaises(DataIntegrityError):
            validate_chart_payload(payload)


if __name__ == "__main__":
    unittest.main()
