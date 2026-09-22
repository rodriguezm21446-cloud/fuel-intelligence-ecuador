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
from fuelintel.market_intelligence import (
    _rolling_volatility,
    _plausible_market_series,
    _weekly_closes,
    build_market_briefing,
    build_market_heatmap_payload,
    build_market_payload,
    build_market_sections,
    build_refined_products_payload,
    build_us_retail_payload,
    build_weekly_moves_payload,
    choose_market_focus,
    estimate_direction,
    select_event_chart_key,
    build_volatility_payload,
)
from fuelintel.storage import Store


class ChartTests(unittest.TestCase):
    def test_market_briefing_labels_prediction_as_experimental(self):
        rows = [(f"2026-08-{day:02d}", 70.0 + day / 10) for day in range(1, 22)]
        message = build_market_briefing({"WTI": rows, "Brent": rows, "RBOB": rows, "ULSD": rows})
        self.assertIn("Qué pasó en el mercado", message)
        self.assertIn("no es el precio oficial", message)
        self.assertNotIn("índice direccional", message)
        self.assertIn("Noticias que pueden estar relacionadas", message)
        self.assertIn("Lo más importante", message)

    def test_us_retail_squawk_preserves_every_latest_price_and_stays_short(self):
        market_rows = [(f"2026-08-{day:02d}", 70.0 + day / 10) for day in range(1, 22)]
        retail = {
            "Gasolina regular": [("2026-09-07", 4.201), ("2026-09-14", 4.319)],
            "Gasolina premium": [("2026-09-07", 5.188), ("2026-09-14", 5.316)],
            "Diésel": [("2026-09-07", 6.101), ("2026-09-14", 6.285)],
        }
        sections = build_market_sections({"WTI": market_rows}, us_retail=retail)
        self.assertLessEqual(len(sections.squawk), 300)
        for expected in ("4.319", "5.316", "6.285", "14/09/2026"):
            self.assertIn(expected, sections.squawk)

    def test_market_briefing_attributes_related_news_without_inventing_causality(self):
        rows = [(f"2026-08-{day:02d}", 70.0 + day / 10) for day in range(1, 22)]
        message = build_market_briefing(
            {"WTI": rows},
            [{"source_name": "EIA", "title": "Inventarios semanales", "canonical_url": "https://eia.example/news"}],
        )
        self.assertIn("Según EIA", message)
        self.assertIn("https://eia.example/news", message)
        self.assertIn("no causalidad confirmada", message)

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
            self.assertEqual(payload["title"], "Precio vigente de combustible en Ecuador")
            self.assertEqual(payload["datasets"][0]["color"], "#8A8A8A")
            self.assertEqual(payload["theme"], "dark")
            self.assertEqual(payload["chartKind"], "step")
            self.assertIn("Chart", build_chart_html(payload))
            destination = root / "dashboard.png"
            with patch("fuelintel.charts.render_chart", return_value=destination):
                self.assertEqual(generate_dashboard(store, destination, datetime.now(timezone.utc)), destination)

    def test_market_payload_is_sentence_case_and_not_smoothed(self):
        payload = build_market_payload(
            {"WTI": [("2026-08-03", 70.0), ("2026-08-10", 71.0)], "Brent": [("2026-08-03", 74.0), ("2026-08-10", 75.0)]}
        )
        self.assertEqual(payload["title"], "Petróleo: WTI y Brent")
        self.assertIn("cierres diarios", payload["subtitle"])
        self.assertEqual(payload["displayDecimals"], 2)
        self.assertIn("No implica por sí solo", payload["insight"])

    def test_refined_products_have_same_units_and_three_decimals(self):
        rows = [(f"2026-08-{day:02d}", 2 + day / 100) for day in range(1, 22)]
        payload = build_refined_products_payload({"RBOB": rows, "ULSD": rows})
        self.assertIn("USD por galón", payload["subtitle"])
        self.assertEqual(payload["displayDecimals"], 3)
        self.assertIn("No son precios oficiales de Ecuador", payload["insight"])

    def test_us_retail_chart_uses_professional_measure_description(self):
        rows = [("2026-09-07", 4.1), ("2026-09-14", 4.2)]
        payload = build_us_retail_payload({"Gasolina regular": rows, "Diésel": rows})
        self.assertIn("Promedios minoristas", payload["insight"])
        self.assertNotIn("no confundir", payload["insight"].casefold())
        self.assertIn("publicación semanal", payload["source"])

    def test_volatility_uses_returns_and_is_not_a_direction_signal(self):
        rows = [(f"2026-08-{day:02d}", 70 + day * 0.2) for day in range(1, 29)]
        self.assertTrue(_rolling_volatility(rows, window=5))
        payload = build_volatility_payload({"WTI": rows, "Brent": rows})
        self.assertEqual(payload["valueSuffix"], "%")
        self.assertIn("no dirección", payload["insight"])

    def test_market_series_rejects_ticker_collisions_and_wrong_units(self):
        days = [(f"2026-08-{day:02d}", 80.0) for day in range(1, 31)]
        self.assertTrue(_plausible_market_series("Brent", days))
        self.assertFalse(_plausible_market_series("Brent", [(day, 16.0) for day, _ in days]))
        self.assertFalse(_plausible_market_series("RBOB", [(day, 46.0) for day, _ in days]))
        self.assertTrue(_plausible_market_series("ULSD", [(day, 4.9) for day, _ in days]))

    def test_heatmap_compares_returns_not_incompatible_price_levels(self):
        rows = [(f"2026-08-{day:02d}", 70 + day) for day in range(1, 31)]
        payload = build_market_heatmap_payload({"WTI": rows, "Brent": rows})
        self.assertEqual([row["label"] for row in payload["rows"]], ["WTI", "Brent"])
        self.assertIn("no mezcla USD por barril", payload["insight"])

    def test_weekly_moves_use_bars_and_percentages(self):
        rows = [(f"2026-08-{day:02d}", 70 + day) for day in range(1, 31)]
        payload = build_weekly_moves_payload({"WTI": rows, "Brent": rows})
        self.assertEqual(payload["chartKind"], "bar")
        self.assertEqual(payload["valueSuffix"], "%")

    def test_prediction_is_withheld_without_enough_history(self):
        rows = [(f"2026-08-{day:02d}", 70 + day / 10) for day in range(1, 31)]
        result = estimate_direction(rows)
        self.assertFalse(result.publishable)
        self.assertIn("80", result.reason)

    def test_event_chart_selection_is_deterministic_and_varies_by_subject(self):
        self.assertEqual(select_event_chart_key("Volatilidad del crudo aumenta"), "volatility")
        self.assertEqual(select_event_chart_key("Refinería reduce producción de diésel"), "products")
        self.assertEqual(select_event_chart_key("Selloff en el mercado energético"), "heatmap")
        self.assertEqual(select_event_chart_key("Balance semanal del petróleo"), "weekly-moves")
        self.assertEqual(select_event_chart_key("OPEP revisa oferta de crudo"), "crude")

    def test_market_focus_uses_relevant_news_instead_of_always_same_chart(self):
        rows = [(f"2026-08-{day:02d}", 70 + day / 10) for day in range(1, 31)]
        focus = choose_market_focus(
            {"WTI": rows, "Brent": rows, "RBOB": rows, "ULSD": rows},
            [{"title": "Aumenta la volatilidad del petróleo"}],
        )
        self.assertEqual(focus, "volatility")


if __name__ == "__main__":
    unittest.main()
