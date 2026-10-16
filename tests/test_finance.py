from __future__ import annotations

from datetime import date, timedelta
import unittest

from fuelintel.finance import (
    beta,
    log_returns,
    max_drawdown,
    moving_average,
    percentile_rank,
    realized_volatility,
    return_correlation,
)
from fuelintel.knowledge import CONCEPTS, concept
from fuelintel.market_intelligence import build_market_sections


def series(values):
    start = date(2026, 1, 1)
    return [((start + timedelta(days=index)).isoformat(), float(value)) for index, value in enumerate(values)]


class FinanceMathTests(unittest.TestCase):
    def test_log_returns_skip_non_positive_prices(self):
        rows = [("2026-01-01", 100.0), ("2026-01-02", 110.0), ("2026-01-03", 0.0)]
        self.assertEqual([day for day, _ in log_returns(rows)], ["2026-01-02"])

    def test_constant_series_has_zero_volatility(self):
        self.assertEqual(realized_volatility(series([70.0] * 30), 20), 0.0)

    def test_volatility_needs_enough_observations(self):
        self.assertIsNone(realized_volatility(series([70.0, 71.0, 72.0]), 20))

    def test_percentile_rank_places_the_last_value(self):
        self.assertEqual(percentile_rank(series(list(range(1, 21)))), 100)
        self.assertIsNone(percentile_rank(series([1.0, 2.0, 3.0])))

    def test_correlation_of_a_series_with_itself_is_one(self):
        rows = series([100 + index for index in range(61)])
        self.assertEqual(return_correlation(rows, rows, 60), 1.0)

    def test_correlation_of_inverted_series_is_minus_one(self):
        up = series([100 + index for index in range(61)])
        down = series([1 / (100 + index) for index in range(61)])
        self.assertEqual(return_correlation(up, down, 60), -1.0)

    def test_beta_of_a_series_against_itself_is_one(self):
        rows = series([100 + index for index in range(61)])
        self.assertEqual(beta(rows, rows, 60), 1.0)

    def test_max_drawdown_is_a_negative_percentage(self):
        self.assertEqual(max_drawdown(series([100.0, 120.0, 90.0, 110.0])), -25.0)

    def test_moving_average_uses_the_last_window(self):
        self.assertEqual(moving_average(series([1, 2, 3, 4, 5]), 3), 4.0)


class ConceptGlossaryTests(unittest.TestCase):
    def test_every_concept_states_definition_formula_and_use(self):
        for spec in CONCEPTS.values():
            self.assertTrue(spec.definition)
            self.assertTrue(spec.formula)
            self.assertTrue(spec.applied_as)

    def test_expected_concepts_are_documented(self):
        for key in ("retorno", "volatilidad", "correlacion", "beta", "percentil", "drawdown", "crack_spread"):
            self.assertIsNotNone(concept(key), key)


class AppliedReadingTests(unittest.TestCase):
    def test_market_sections_publish_a_measured_reading_without_forecasting(self):
        rows = series([70 + index / 10 for index in range(80)])
        sections = build_market_sections({"WTI": rows, "RBOB": rows, "ULSD": rows})
        context = "\n".join(sections.messages())
        self.assertIn("Lectura cuantitativa", context)
        self.assertIn("Volatilidad realizada", context)
        self.assertIn("Correlación de retornos", context)
        self.assertIn("sin pronóstico", context)


if __name__ == "__main__":
    unittest.main()
