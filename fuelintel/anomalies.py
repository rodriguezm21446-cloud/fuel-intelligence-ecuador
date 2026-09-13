from __future__ import annotations

from dataclasses import dataclass
from math import exp, isfinite, log
from statistics import median


@dataclass(frozen=True, slots=True)
class AnomalySignal:
    series: str
    kind: str
    severity: int
    explanation: str


def _returns(rows: list[tuple[str, float]]) -> list[float]:
    values = [float(value) for _, value in rows if isfinite(float(value)) and float(value) > 0]
    return [log(current / previous) for previous, current in zip(values, values[1:])]


def _robust_z(sample: list[float], value: float) -> float:
    """Median/MAD score, stable when a few market observations are extreme."""
    if len(sample) < 10:
        return 0.0
    center = median(sample)
    mad = median(abs(item - center) for item in sample)
    if mad <= 1e-12:
        return 0.0
    return 0.67448975 * (value - center) / mad


def detect_anomalies(raw_series: dict[str, list[tuple[str, float]]]) -> list[AnomalySignal]:
    """Detect unusual latest moves and regime shifts without forecasting direction."""
    names = {"WTI": "WTI", "Brent": "Brent", "RBOB": "gasolina RBOB", "ULSD": "diésel ULSD"}
    signals: list[AnomalySignal] = []
    for key, rows in raw_series.items():
        returns = _returns(rows)
        if len(returns) < 25:
            continue
        latest = returns[-1]
        score = _robust_z(returns[-61:-1], latest)
        latest_pct = (exp(latest) - 1) * 100
        if abs(score) >= 3.5:
            direction = "subida" if latest > 0 else "bajada"
            signals.append(AnomalySignal(
                key, "movimiento_atipico", min(100, round(abs(score) * 18)),
                f"{names.get(key, key)} tuvo una {direction} diaria poco común ({latest_pct:+.1f}%).",
            ))

        recent = returns[-5:]
        baseline = returns[-25:-5]
        regime_score = _robust_z(baseline, sum(recent) / len(recent))
        if abs(regime_score) >= 3.0 and abs(sum(recent)) >= 0.03:
            direction = "más alcista" if sum(recent) > 0 else "más bajista"
            signals.append(AnomalySignal(
                key, "cambio_regimen", min(100, round(abs(regime_score) * 15)),
                f"El comportamiento reciente de {names.get(key, key)} pasó a ser {direction} que en las semanas anteriores.",
            ))
    return sorted(signals, key=lambda item: item.severity, reverse=True)
