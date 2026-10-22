from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fuelintel import yahoo
from fuelintel.market_intelligence import _LAST_PROVIDERS, _history_dnav, load_market_series

FIXTURE = Path(__file__).parent / "fixtures" / "ulsdd.xls"


def _payload(closes: list[float]) -> dict:
    return {
        "chart": {
            "result": [{
                "timestamp": [1760000000 + index * 86400 for index in range(len(closes))],
                "indicators": {"quote": [{"close": closes}], "adjclose": [{"adjclose": closes}]},
            }]
        }
    }


class YahooRowsTests(unittest.TestCase):
    def test_observed_closes_are_sorted_and_deduplicated(self):
        rows = yahoo._rows_from_payload(_payload([50.0, 50.5, 51.0]))
        self.assertEqual([day for day, _ in rows], sorted({day for day, _ in rows}))
        self.assertEqual([value for _, value in rows], [50.0, 50.5, 51.0])

    def test_single_session_glitch_is_dropped(self):
        rows = yahoo._rows_from_payload(_payload([50.0, 65.0, 51.0]))
        self.assertEqual([value for _, value in rows], [50.0, 51.0])

    def test_null_closes_are_skipped(self):
        rows = yahoo._rows_from_payload(_payload([50.0, None, 51.0]))  # type: ignore[list-item]
        self.assertEqual([value for _, value in rows], [50.0, 51.0])

    def test_failed_host_returns_empty_not_a_guess(self):
        with patch("fuelintel.yahoo._fetch_host", return_value=None) as probe:
            self.assertEqual(yahoo.fetch_daily_close("CL=F"), [])
            self.assertEqual(probe.call_count, len(yahoo._API_HOSTS))


def _yahoo_rows(count: int = 30) -> list[tuple[str, float]]:
    return [
        (f"2026-{8 + day // 28:02d}-{1 + day % 28:02d}", 60.0 + day / 10)
        for day in range(count)
    ]


class MarketLadderTests(unittest.TestCase):
    def setUp(self):
        _LAST_PROVIDERS.clear()

    @patch.dict(os.environ, {"EIA_API_KEY": ""})
    def test_yahoo_lane_serves_series_before_eia(self):
        yahoo_rows = _yahoo_rows()
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {"FUELINTEL_DATA_DIR": folder}):
                with patch("fuelintel.market_intelligence._history_yahoo", return_value=yahoo_rows), \
                     patch("fuelintel.market_intelligence._history", side_effect=RuntimeError), \
                     patch("fuelintel.market_intelligence._history_dnav", side_effect=RuntimeError):
                    series = load_market_series()
            self.assertIn("WTI", series)
            self.assertEqual(series["WTI"], yahoo_rows)
            self.assertEqual(_LAST_PROVIDERS, {"Yahoo Finance (futuros CME)"})

    @patch.dict(os.environ, {"EIA_API_KEY": ""})
    def test_yahoo_outage_degrades_to_dnav_with_official_attribution(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {"FUELINTEL_DATA_DIR": folder}):
                with patch("fuelintel.market_intelligence._history_yahoo", return_value=[]), \
                     patch("fuelintel.market_intelligence._history", return_value=[]), \
                     patch(
                         "fuelintel.market_intelligence._history_dnav",
                         side_effect=lambda name, **kw: _history_dnav(name, body=FIXTURE.read_bytes()),
                     ):
                    series = load_market_series()
            self.assertIn("ULSD", series)
            self.assertEqual(_LAST_PROVIDERS, set())
            self.assertEqual(
                series["ULSD"][-1][0],
                _history_dnav("ULSD", body=FIXTURE.read_bytes())[-1][0],
            )

    @patch.dict(os.environ, {"EIA_API_KEY": ""})
    def test_mixed_lanes_declare_both_attributions(self):
        yahoo_rows = _yahoo_rows()
        dnav_rows = _history_dnav("ULSD", body=FIXTURE.read_bytes())
        if len(dnav_rows) > 400:
            dnav_rows = dnav_rows[-400:]
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {"FUELINTEL_DATA_DIR": folder}):
                with patch("fuelintel.market_intelligence._history_yahoo", side_effect=lambda name: yahoo_rows if name == "WTI" else []), \
                     patch("fuelintel.market_intelligence._history", return_value=[]), \
                     patch(
                         "fuelintel.market_intelligence._history_dnav",
                         side_effect=lambda name, **kw: dnav_rows,
                     ):
                    series = load_market_series()
            self.assertIn("WTI", series)
            self.assertIn("ULSD", series)
            self.assertEqual(_LAST_PROVIDERS, {"Yahoo Finance (futuros CME)", "U.S. EIA"})


if __name__ == "__main__":
    unittest.main()
