from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from fuelintel.forecasting import forecast_next_price, format_forecast_message
from fuelintel.models import EffectivePeriod, FuelPrice, PriceType
from fuelintel.storage import Store


class ForecastingTests(unittest.TestCase):
    def rows(self, values: list[str]):
        return [
            {"effective_from": f"2026-{index + 1:02d}-01", "observed_at": f"2026-{index + 1:02d}-01T00:00:00+00:00", "price": value}
            for index, value in enumerate(values)
        ]

    def test_refuses_to_guess_with_short_history(self):
        result = forecast_next_price("Extra", self.rows(["2.40"] * 4))
        self.assertFalse(result.publishable)
        self.assertIn("historial insuficiente", result.reason)

    def test_publishes_only_after_beating_no_change_backtest(self):
        values = [str(Decimal("2.00") + Decimal(index) * Decimal("0.03")) for index in range(12)]
        result = forecast_next_price("Extra", self.rows(values))
        self.assertTrue(result.publishable)
        self.assertLess(result.model_mae, result.naive_mae)
        self.assertEqual(result.probability_up + result.probability_flat + result.probability_down, 100)
        message = format_forecast_message(result)
        self.assertIn("ESCENARIO, NO PRECIO OFICIAL", message)
        self.assertIn("error medio", message)
        self.assertEqual(result.target_effective_from, date(2027, 1, 1))

    def test_next_official_period_scores_previous_forecast(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "db.sqlite3")
            values = [str(Decimal("2.00") + Decimal(index) * Decimal("0.03")) for index in range(12)]
            result = forecast_next_price("Extra", self.rows(values))
            store.record_forecast(result)
            self.assertIsNotNone(result.target_effective_from)
            store.save_price(
                FuelPrice(
                    "Extra", Decimal("2.390"),
                    period=EffectivePeriod(result.target_effective_from, result.target_effective_from + timedelta(days=29)),
                    source_url="https://example.test/next.pdf",
                    price_type=PriceType.PUBLICO,
                    source_name="ARCH",
                )
            )
            with store.connect() as db:
                row = db.execute(
                    "SELECT actual_price, absolute_error, resolved_at FROM forecast_runs ORDER BY id DESC LIMIT 1"
                ).fetchone()
            self.assertEqual(row["actual_price"], "2.390")
            self.assertIsNotNone(row["absolute_error"])
            self.assertIsNotNone(row["resolved_at"])

    def test_timestamp_fields_are_not_swapped(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "db.sqlite3")
            observed = datetime(2026, 9, 11, 20, 1, tzinfo=timezone.utc)
            verified = observed + timedelta(minutes=9)
            store.save_price(
                FuelPrice(
                    "Extra", Decimal("3.210"), period=EffectivePeriod(date(2026, 9, 12), date(2026, 10, 11)),
                    source_url="https://example.test/official.pdf", observed_at=observed, verified_at=verified,
                    price_type=PriceType.PUBLICO, source_name="ARCH",
                )
            )
            db = sqlite3.connect(store.path)
            try:
                row = db.execute("SELECT observed_at, verified_at FROM fuel_prices").fetchone()
            finally:
                db.close()
            self.assertEqual(row[0], observed.isoformat())
            self.assertEqual(row[1], verified.isoformat())


if __name__ == "__main__":
    unittest.main()
