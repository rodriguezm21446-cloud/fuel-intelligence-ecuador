from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from fuelintel.chart_renderer import build_chart_html
from fuelintel.charts import build_national_payload
from fuelintel.models import EffectivePeriod, FuelPrice, PriceType
from fuelintel.storage import Store


def official_price(
    product: str,
    value: str,
    effective: date,
    *,
    source: str = "ARCH",
    status: str = "CONFIRMED",
) -> FuelPrice:
    stamp = datetime(effective.year, effective.month, effective.day, 15, 30, tzinfo=timezone.utc)
    return FuelPrice(
        product=product,
        price=Decimal(value),
        period=EffectivePeriod(effective, effective),
        source_url=f"https://{source.lower()}.example/{effective.isoformat()}",
        observed_at=stamp,
        verified_at=stamp,
        price_type=PriceType.PUBLICO,
        source_name=source,
        verification_status=status,
    )


class DataArchitectureTests(unittest.TestCase):
    def test_metrics_are_recomputed_on_every_insert(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "db.sqlite3")
            store.save_price(official_price("Extra", "2.000", date(2025, 9, 12)))
            store.save_price(official_price("Extra", "2.400", date(2026, 8, 12)))
            store.save_price(official_price("Extra", "2.500", date(2026, 9, 12)))

            metric = store.price_metrics()[0]
            self.assertEqual(metric["historical_min"], "2.000")
            self.assertEqual(metric["historical_min_date"], "2025-09-12")
            self.assertEqual(metric["historical_max"], "2.500")
            self.assertEqual(metric["historical_max_date"], "2026-09-12")
            self.assertEqual(metric["mom_absolute"], "0.100")
            self.assertEqual(metric["mom_percent"], "4.17")
            self.assertEqual(metric["yoy_absolute"], "0.500")
            self.assertEqual(metric["yoy_percent"], "25.00")

            metric_event = next(row for row in store.pending_sync() if row["entity_type"] == "metrics")
            self.assertEqual(json.loads(metric_event["payload_json"])["latest_price"], "2.500")

    def test_source_conflict_removes_series_from_publishable_data(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "db.sqlite3")
            arch = official_price("Extra", "2.400", date(2026, 9, 12), source="ARCH")
            petro = official_price("Extra", "2.500", date(2026, 9, 12), source="Petroecuador")
            store.save_price(arch)
            self.assertTrue(store.price_conflict(petro))
            store.save_price(petro)
            store.mark_source_conflict(petro)
            self.assertEqual(store.recent_prices(), [])
            self.assertEqual(store.price_metrics(), [])
            self.assertIn("metrics_delete", {row["entity_type"] for row in store.pending_sync()})

    def test_axis_ends_at_last_confirmed_date(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "db.sqlite3")
            store.save_price(official_price("Extra", "2.400", date(2026, 8, 12)))
            store.save_price(official_price("Extra", "2.500", date(2026, 9, 12), status="PENDING"))
            payload = build_national_payload(store)
            self.assertEqual(payload["labels"][-1], "12/08/2026")
            self.assertIn("Verificado:", payload["source"])
            self.assertIn("ECT", payload["source"])

    def test_html_enforces_editorial_style(self):
        payload = {
            "title": "Precios de combustible en Ecuador",
            "subtitle": "Últimos cuatro periodos",
            "labels": ["12/08/2026"],
            "datasets": [{"label": "Diésel", "color": "#4A90D9", "values": [2.4], "pending": False}],
            "annotations": [],
            "references": [],
            "stepped": True,
            "decimals": 3,
            "insight": "Dato confirmado.",
            "source": "Fuente: ARCH · Verificado: 12/08/2026 10:00 ECT",
            "credit": "Cálculos y visualización: @PreciosCombustibleEC",
        }
        html = build_chart_html(payload)
        self.assertIn("font-family: Inter, Arial, sans-serif", html)
        self.assertIn("position: 'right'", html)
        self.assertIn("grid: { display: false }", html)
        for forbidden in ("Courier", "Consolas", "monospace", "linear-gradient", "box-shadow", "text-shadow"):
            self.assertNotIn(forbidden, html)


if __name__ == "__main__":
    unittest.main()

