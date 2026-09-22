from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from fuelintel.intraday import (
    IntradayAlertGate,
    IntradaySignal,
    build_intraday_messages,
    detect_intraday_signals,
)
from fuelintel.storage import Store


NOW = datetime(2026, 9, 21, 1, 0, tzinfo=timezone.utc)


def minute_series(changes: list[float], start: float = 80.0):
    value = start
    rows = []
    first = NOW - timedelta(minutes=len(changes))
    rows.append((first, value, 100.0))
    for index, change in enumerate(changes, start=1):
        value *= 1 + change
        rows.append((first + timedelta(minutes=index), value, 100.0 + index))
    return rows


class IntradayTests(unittest.TestCase):
    def test_detects_large_latest_minute_move(self):
        normal = [0.0004, -0.0003, 0.0002, -0.0005, 0.0001] * 25
        signals = detect_intraday_signals({"WTI": minute_series(normal + [0.015])}, now=NOW)
        self.assertTrue(any(signal.kind == "salto_1m" for signal in signals))

    def test_stale_market_data_never_triggers_an_alert(self):
        normal = [0.0004, -0.0003, 0.0002, -0.0005, 0.0001] * 25
        rows = minute_series(normal + [0.015])
        signals = detect_intraday_signals({"WTI": rows}, now=NOW + timedelta(hours=1))
        self.assertEqual(signals, [])

    def test_gate_requires_confirmation_and_enforces_cooldown(self):
        signal = IntradaySignal("WTI", "salto_1m", NOW, 81.2, 1.5, 8.0, 100, "salto")
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "db.sqlite3")
            gate = IntradayAlertGate(confirmations=2, cooldown_minutes=30)
            self.assertFalse(gate.accept(signal, store, NOW))
            self.assertTrue(gate.accept(signal, store, NOW + timedelta(minutes=1)))
            self.assertFalse(gate.accept(signal, store, NOW + timedelta(minutes=2)))

    def test_alert_for_x_is_short_and_does_not_invent_a_cause(self):
        signal = IntradaySignal("ULSD", "movimiento_5m", NOW, 5.058, 1.25, 5.2, 90, "ULSD subió 1.25%.")
        squawk, context = build_intraday_messages(signal, [], NOW)
        self.assertLessEqual(len(squawk), 300)
        self.assertIn("$5.058/gal", squawk)
        self.assertIn("+1.25%", squawk)
        self.assertIn("No se encontró", context)
        self.assertNotIn("debido a", squawk.casefold())

    def test_market_tick_is_persisted_once_per_symbol_and_minute(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "db.sqlite3")
            store.save_market_tick("WTI", "CL=F", NOW, 80.1, 100.0, NOW)
            store.save_market_tick("WTI", "CL=F", NOW, 80.2, 120.0, NOW + timedelta(seconds=10))
            with store.connect() as db:
                row = db.execute("SELECT COUNT(*) AS total, price FROM market_ticks").fetchone()
            self.assertEqual(row["total"], 1)
            self.assertEqual(row["price"], "80.2")


if __name__ == "__main__":
    unittest.main()
