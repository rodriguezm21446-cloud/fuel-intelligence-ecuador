from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from fuelintel.chart_renderer import build_chart_html
from fuelintel.charts import build_national_payload, choose_chart_kind, generate_dashboard, own_data_label
from fuelintel.models import EffectivePeriod, FuelPrice, PriceType
from fuelintel.openbb_chart import _weekly_closes, build_market_payload
from fuelintel.storage import Store


class ChartTests(unittest.TestCase):
    def test_chart_kind_is_selected_from_the_data(self):
        self.assertEqual(choose_chart_kind("official_period_price", 5), "step")
        self.assertEqual(choose_chart_kind("market_price", 50, has_ohlc=True), "candlestick")
        self.assertEqual(choose_chart_kind("market_price", 50, has_ohlc=False), "line")
        self.assertEqual(choose_chart_kind("comparison", 4), "bar")
        self.assertEqual(choose_chart_kind("market_price", 1), "card")

    def test_estimate_is_never_labeled_as_official(self):
        label = own_data_label(estimated=True)
        self.assertIn("Estimación propia", label)
        self.assertIn("No es dato oficial", label)

    def test_weekly_chart_uses_last_real_close_without_smoothing(self):
        rows = [
            ("2026-08-03", 70.0),
            ("2026-08-04", 71.0),
            ("2026-08-07", 69.5),
            ("2026-08-10", 72.0),
        ]
        self.assertEqual(_weekly_closes(rows), [("2026-08-07", 69.5), ("2026-08-10", 72.0)])

    def test_dashboard_builds_chartjs_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = Store(root / "db.sqlite3")
            store.save_price(
                FuelPrice(
                    "Extra",
                    Decimal("2.400"),
                    period=EffectivePeriod(date(2026, 8, 12), date(2026, 9, 11)),
                    source_url="https://example.test",
                    price_type=PriceType.PUBLICO,
                    source_name="ARCH",
                )
            )
            payload = build_national_payload(store)
            self.assertEqual(payload["title"], "Precios de combustible en Ecuador")
            self.assertEqual(payload["datasets"][0]["color"], "#8A8A8A")
            self.assertIn("Chart", build_chart_html(payload))
            destination = root / "dashboard.png"
            with patch("fuelintel.charts.render_chart", return_value=destination):
                self.assertEqual(generate_dashboard(store, destination, datetime.now(timezone.utc)), destination)

    def test_market_payload_is_sentence_case_and_not_smoothed(self):
        payload = build_market_payload(
            {"WTI": [("2026-08-03", 70.0), ("2026-08-10", 71.0)], "Brent": [("2026-08-03", 74.0), ("2026-08-10", 75.0)]}
        )
        self.assertEqual(payload["title"], "Petróleo: WTI y Brent")
        self.assertIn("No implica por sí solo", payload["insight"])


if __name__ == "__main__":
    unittest.main()

