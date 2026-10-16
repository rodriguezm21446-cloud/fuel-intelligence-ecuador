from __future__ import annotations

import unittest

from fuelintel.knowledge import band_text, within_band
from fuelintel.quality import describe_drop, quarantine


class KnowledgeTests(unittest.TestCase):
    def test_bands_flag_an_impossible_diesel_crack(self):
        self.assertFalse(within_band("crack_diesel", 105.0))
        self.assertTrue(within_band("crack_diesel", 25.0))
        self.assertFalse(within_band("ULSD", 0.1))

    def test_unknown_series_is_never_blocked(self):
        self.assertTrue(within_band("promedio_inventado", 99999.0))

    def test_band_text_states_unit(self):
        self.assertEqual(band_text("crack_diesel"), "-20–70 USD/barril")


class QuarantineTests(unittest.TestCase):
    def test_out_of_band_and_non_finite_points_are_removed_with_a_reason(self):
        rows = [
            ("2026-09-14", 30.0),
            ("2026-09-15", float("nan")),
            ("2026-09-16", 105.0),
            ("2026-09-17", 32.0),
        ]
        result = quarantine("crack_diesel", rows)
        self.assertEqual([day for day, _ in result.kept], ["2026-09-14", "2026-09-17"])
        self.assertEqual(result.removed, 2)
        self.assertIn("fuera del rango normal", result.reasons())
        self.assertIn("no finito", result.reasons())
        self.assertIn("2 punto(s) descartado(s)", describe_drop(result))

    def test_a_clean_series_reports_nothing(self):
        rows = [("2026-09-14", 30.0), ("2026-09-15", 31.0)]
        result = quarantine("crack_diesel", rows)
        self.assertEqual(result.kept, [("2026-09-14", 30.0), ("2026-09-15", 31.0)])
        self.assertEqual(describe_drop(result), "")


if __name__ == "__main__":
    unittest.main()
