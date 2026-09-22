from __future__ import annotations

import unittest
from datetime import date, timedelta

from fuelintel.anomalies import detect_anomalies


def series(changes: list[float]) -> list[tuple[str, float]]:
    day = date(2026, 1, 1)
    value = 80.0
    rows = [(day.isoformat(), value)]
    for index, change in enumerate(changes, start=1):
        value *= 1 + change
        rows.append(((day + timedelta(days=index)).isoformat(), value))
    return rows


class AnomalyTests(unittest.TestCase):
    def test_large_latest_move_is_flagged(self):
        normal = [0.002, -0.001, 0.003, -0.002, 0.001] * 12
        signals = detect_anomalies({"Brent": series(normal + [0.12])})
        self.assertTrue(any(item.kind == "movimiento_atipico" for item in signals))

    def test_normal_noise_is_not_flagged_as_extreme(self):
        normal = [0.002, -0.001, 0.003, -0.002, 0.001] * 13
        signals = detect_anomalies({"WTI": series(normal)})
        self.assertFalse(any(item.kind == "movimiento_atipico" for item in signals))


if __name__ == "__main__":
    unittest.main()
