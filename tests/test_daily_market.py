from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from fuelintel.formatting import TelegramSections
from fuelintel.main import dashboard
from fuelintel.market_intelligence import (
    build_brent_wti_spread_payload,
    build_indexed_energy_payload,
    generate_market_bundle,
    select_event_chart_key,
)
from fuelintel.storage import Store


class _Notifier:
    def __init__(self):
        self.photos = []
        self.texts = []

    def send_photo(self, path, caption):
        self.photos.append((path, caption))
        return True

    def send_text(self, message):
        self.texts.append(message)
        return True


class DailyMarketTests(unittest.TestCase):
    def test_scheduled_delivery_sends_daily_and_repeats_only_on_verified_change(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = Store(root / "db.sqlite3")
            notifier = _Notifier()
            settings = SimpleNamespace(
                data_dir=root,
                timezone=timezone(timedelta(hours=-5)),
                eia_api_key="",
                chart_revision="8",
            )
            chart = root / "market.png"
            chart.write_bytes(b"png")
            sections = TelegramSections("Titular comprobado", "Contexto comprobado")
            bundle = ([('products', chart, 'same-data-signature')], sections, 'products', '2026-09-18')
            changed = ([('products', chart, 'new-data-signature')], sections, 'products', '2026-09-20')
            day_one = datetime(2026, 9, 20, 18, 17, tzinfo=settings.timezone)
            with patch("fuelintel.main.build_runtime", return_value=(store, None, notifier, None)), \
                 patch("fuelintel.main.national_data_signature", return_value=None), \
                 patch("fuelintel.main.generate_market_bundle", side_effect=[bundle, bundle, bundle, changed]) as generated:
                self.assertEqual(dashboard(settings, day_one), 0)
                self.assertEqual(dashboard(settings, day_one + timedelta(hours=1)), 0)
                self.assertEqual(dashboard(settings, day_one + timedelta(days=1)), 0)
                self.assertEqual(dashboard(settings, day_one + timedelta(days=1, hours=1)), 0)
            self.assertEqual(generated.call_count, 4)
            self.assertEqual(len(notifier.photos), 3)
            self.assertIn("Último dato observado: 2026-09-18", notifier.photos[0][1])
            self.assertEqual(notifier.texts[:2], ["Titular comprobado", "Contexto comprobado"])
            self.assertEqual(notifier.texts[-2:], ["Titular comprobado", "Contexto comprobado"])

    def test_spread_uses_only_dates_shared_by_both_markets(self):
        raw = {
            "WTI": [("2026-09-14", 80.0), ("2026-09-15", 82.0), ("2026-09-16", 81.0)],
            "Brent": [("2026-09-14", 84.0), ("2026-09-16", 86.0)],
        }
        payload = build_brent_wti_spread_payload(raw)
        self.assertEqual(payload["labels"], ["14/09/2026", "16/09/2026"])
        self.assertEqual(payload["datasets"][0]["values"], [4.0, 5.0])

    def test_indexed_view_uses_common_base_and_clear_units(self):
        raw = {
            "WTI": [("2026-09-14", 80.0), ("2026-09-15", 84.0)],
            "RBOB": [("2026-09-14", 2.0), ("2026-09-15", 2.2)],
        }
        payload = build_indexed_energy_payload(raw)
        self.assertEqual(payload["datasets"][0]["values"], [100.0, 105.0])
        self.assertAlmostEqual(payload["datasets"][1]["values"][0], 100.0)
        self.assertAlmostEqual(payload["datasets"][1]["values"][1], 110.0)
        self.assertIn("Base 100", payload["subtitle"])

    def test_news_can_choose_spread_and_relative_views(self):
        self.assertEqual(select_event_chart_key("Aumenta diferencial Brent-WTI"), "spread")
        self.assertEqual(select_event_chart_key("Comparación relativa entre combustibles"), "indexed")

    def test_daily_bundle_renders_only_selected_chart(self):
        raw = {
            "WTI": [("2026-09-14", 80.0), ("2026-09-15", 81.0)],
            "Brent": [("2026-09-14", 84.0), ("2026-09-15", 85.0)],
        }
        with tempfile.TemporaryDirectory() as folder, \
             patch("fuelintel.market_intelligence.load_market_series", return_value=raw), \
             patch("fuelintel.market_intelligence.choose_market_focus", return_value="spread"), \
             patch("fuelintel.market_intelligence.render_chart", side_effect=lambda payload, path: path) as render:
            artifacts, _, focus, last_day = generate_market_bundle(Path(folder), selected_only=True)
        self.assertEqual([item[0] for item in artifacts], ["spread"])
        self.assertEqual(focus, "spread")
        self.assertEqual(last_day, "2026-09-15")
        self.assertEqual(render.call_count, 1)


if __name__ == "__main__":
    unittest.main()
