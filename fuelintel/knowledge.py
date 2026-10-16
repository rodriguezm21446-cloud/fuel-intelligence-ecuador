"""Domain facts the bot must know before it plots any series.

Every metric the bot draws carries three things here: its unit, what it
actually means, and the band of values that is historically reasonable.  The
quality gate uses the band to refuse numbers that cannot be trusted, and the
writers use the meaning to describe each chart without inventing anything.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MetricFact:
    """What a series is, its unit, and the range that is physically sane."""

    key: str
    unit: str
    meaning: str
    low: float
    high: float


FACTS: dict[str, MetricFact] = {
    "WTI": MetricFact("WTI", "USD/barril", "referencia de crudo estadounidense", 20.0, 250.0),
    "Brent": MetricFact("Brent", "USD/barril", "referencia de crudo europea", 20.0, 250.0),
    "RBOB": MetricFact("RBOB", "USD/galón", "gasolina de mezcla estadounidense", 0.5, 10.0),
    "ULSD": MetricFact("ULSD", "USD/galón", "diésel de ultra bajo azufre", 0.5, 10.0),
    # Refining margins (crack spreads) live well inside this band in normal
    # markets, even in the 2022 squeeze.  Anything far outside is far more
    # likely a bad quote than a real signal, so it is quarantined.
    "crack_gasolina": MetricFact(
        "crack_gasolina", "USD/barril", "margen de refino de gasolina RBOB contra WTI", -20.0, 70.0
    ),
    "crack_diesel": MetricFact(
        "crack_diesel", "USD/barril", "margen de refino de diésel ULSD contra WTI", -20.0, 70.0
    ),
}


def fact(key: str) -> MetricFact | None:
    return FACTS.get(key)


def within_band(key: str, value: float) -> bool:
    """A missing metric is never blocked; an unknown series is trusted as-is."""
    spec = FACTS.get(key)
    if spec is None:
        return True
    return spec.low <= value <= spec.high


def band_text(key: str) -> str:
    spec = FACTS.get(key)
    if spec is None:
        return ""
    return f"{spec.low:g}–{spec.high:g} {spec.unit}"
