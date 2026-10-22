from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fuelintel.market_intelligence import (
    EIA_SPOT_SERIES,
    _history_dnav,
    _load_snapshot,
    _save_snapshot,
    load_market_series,
)

FIXTURE = Path(__file__).parent / "fixtures" / "ulsdd.xls"


class DnavHistoryTests(unittest.TestCase):
    def test_parses_official_model_workbook(self):
        rows = _history_dnav("ULSD", body=FIXTURE.read_bytes())
        self.assertTrue(rows)
        days = [day for day, _ in rows]
        self.assertEqual(days, sorted(set(days)))
        self.assertTrue(all(len(day) == 10 and day[4] == "-" for day in days))
        self.assertGreater(days[-1], "2024-01-01")
        self.assertTrue(all(value > 0 for _, value in rows))

    @patch.dict(os.environ, {"EIA_API_KEY": ""})
    def test_market_ladder_degrades_to_dnav_without_api_key(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {"FUELINTEL_DATA_DIR": folder}):
                with patch("fuelintel.market_intelligence._history", return_value=[]):
                    series = load_market_series()
                self.assertIn("ULSD", series)
                self.assertGreater(series["ULSD"][-1][0], "2024-01-01")
                self.assertIn("ULSD", _load_snapshot())

    def test_provider_outage_falls_back_to_last_honest_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {"FUELINTEL_DATA_DIR": folder}):
                cached = [
                    (f"2026-{month:02d}-{day:02d}", 60.0 + month)
                    for month in range(1, 10)
                    for day in (1, 2, 3, 4, 5)
                ]
                self.assertGreaterEqual(len(cached), 30)
                _save_snapshot({"WTI": (datetime(2026, 1, 1).isoformat(), cached)})
                with patch("fuelintel.market_intelligence._history", side_effect=RuntimeError), \
                     patch("fuelintel.market_intelligence._history_dnav", side_effect=RuntimeError):
                    series = load_market_series()
        self.assertEqual(series["WTI"], cached[-420:])

    def test_series_without_any_source_stays_absent(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {"FUELINTEL_DATA_DIR": folder}):
                with patch("fuelintel.market_intelligence._history", side_effect=RuntimeError), \
                     patch("fuelintel.market_intelligence._history_dnav", side_effect=RuntimeError):
                    series = load_market_series()
        self.assertEqual(series, {})

    def test_rbob_series_points_at_the_current_official_id(self):
        self.assertTrue(EIA_SPOT_SERIES["RBOB"].startswith("EER_EPMRU_PF4"))


if __name__ == "__main__":
    unittest.main()
