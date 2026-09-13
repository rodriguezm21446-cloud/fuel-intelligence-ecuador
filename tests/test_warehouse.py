from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckdb

from fuelintel.warehouse import save_market_history


class WarehouseTests(unittest.TestCase):
    def test_market_history_is_typed_and_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "market.duckdb"
            rows = {"Brent": [("2026-09-10", 101.2), ("2026-09-11", 104.6)]}
            self.assertEqual(save_market_history(path, rows), 2)
            self.assertEqual(save_market_history(path, rows), 2)
            with duckdb.connect(str(path), read_only=True) as db:
                count, latest = db.execute(
                    "SELECT COUNT(*), MAX(close) FROM market_observations"
                ).fetchone()
            self.assertEqual(count, 2)
            self.assertAlmostEqual(latest, 104.6)


if __name__ == "__main__":
    unittest.main()
