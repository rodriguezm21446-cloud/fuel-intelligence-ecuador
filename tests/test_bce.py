from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from fuelintel.bce import fetch_ecuador_crude_exports


def _widget(options: dict) -> str:
    payload = json.dumps({"x": {"hc_opts": options}})
    return f'<script type="application/json" data-for="htmlwidget-abc">{payload}</script>'


def _page(*options: dict) -> bytes:
    return ("<html><body>" + "".join(_widget(item) for item in options) + "</body></html>").encode()


class _Response:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self) -> bytes:
        return self.body


_CRUDE = {
    "title": {"text": "Figura 3. Exportaciones de Petr\u00f3leo Crudo y Derivados"},
    "yAxis": {"title": {"text": "Crudo millones USD - FOB Derivados millones USD - FOB"}},
    "xAxis": {"categories": ["nov-24", "dic-24", "45870", "ene-25", "mal-99"]},
    "series": [
        {"name": "Crudo", "data": [585.2, 678.1, 161.4, 827.3, 999.0]},
        {"name": "Derivados", "data": [45.2, 29.9, 11.2, 50.1, 1.0]},
    ],
}


class BceCrudeExportsTests(unittest.TestCase):
    def test_official_crude_export_value_is_parsed_and_sorted(self):
        with patch("urllib.request.urlopen", return_value=_Response(_page(_CRUDE))):
            rows = fetch_ecuador_crude_exports()
        self.assertEqual(rows, [("2024-11", 585.2), ("2024-12", 678.1), ("2025-01", 827.3), ("2025-08", 161.4)])

    def test_volume_series_is_refused_when_units_are_not_usd_millions(self):
        volume = dict(_CRUDE)
        volume["yAxis"] = {"title": {"text": "Millones de barriles"}}
        with patch("urllib.request.urlopen", return_value=_Response(_page(volume))):
            self.assertEqual(fetch_ecuador_crude_exports(), [])

    def test_page_without_the_crude_figure_yields_nothing(self):
        other = {
            "title": {"text": "Figura 2. Exportaciones totales"},
            "yAxis": {"title": {"text": "Millones de USD"}},
            "xAxis": {"categories": ["nov-24"]},
            "series": [{"name": "Petroleras", "data": [1.0]}],
        }
        with patch("urllib.request.urlopen", return_value=_Response(_page(other))):
            self.assertEqual(fetch_ecuador_crude_exports(), [])

    def test_network_failure_is_never_charted(self):
        with patch("urllib.request.urlopen", side_effect=OSError("sin red")):
            self.assertEqual(fetch_ecuador_crude_exports(), [])


if __name__ == "__main__":
    unittest.main()
