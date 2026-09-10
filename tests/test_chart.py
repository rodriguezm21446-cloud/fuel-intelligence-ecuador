from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from PIL import Image

from fuelintel.charts import HEIGHT, WIDTH, choose_chart_kind, generate_dashboard
from fuelintel.models import EffectivePeriod, FuelPrice
from fuelintel.storage import Store


class ChartTests(unittest.TestCase):
    def test_chart_kind_is_selected_from_the_data(self):
        self.assertEqual(choose_chart_kind("official_period_price", 5), "step")
        self.assertEqual(choose_chart_kind("market_price", 50, has_ohlc=True), "candlestick")
        self.assertEqual(choose_chart_kind("market_price", 50, has_ohlc=False), "line")
        self.assertEqual(choose_chart_kind("comparison", 4), "bar")
        self.assertEqual(choose_chart_kind("market_price", 1), "card")

    def test_dashboard_is_a_valid_png(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = Store(root / "db.sqlite3")
            store.save_price(
                FuelPrice(
                    "Extra",
                    Decimal("2.400"),
                    period=EffectivePeriod(date(2026, 8, 12), date(2026, 9, 11)),
                    source_url="https://example.test",
                )
            )
            path = generate_dashboard(store, root / "dashboard.png", datetime.now(timezone.utc))
            self.assertIsNotNone(path)
            with Image.open(path) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, (WIDTH, HEIGHT))


if __name__ == "__main__":
    unittest.main()

