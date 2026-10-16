"""Quality gate: no number reaches a chart unless it survives this filter.

It never invents, clamps or rounds a suspect value into range.  A point that is
non-finite or outside the metric's sane band is removed and the reason is kept,
so the writer can say exactly what was left out and why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite

from .knowledge import fact, within_band


@dataclass(frozen=True, slots=True)
class Quarantine:
    """Outcome of filtering a single series."""

    kept: list[tuple[str, float]] = field(default_factory=list)
    dropped: list[tuple[str, float, str]] = field(default_factory=list)

    @property
    def removed(self) -> int:
        return len(self.dropped)

    def reasons(self) -> str:
        if not self.dropped:
            return ""
        return "; ".join(sorted({reason for _, _, reason in self.dropped}))


def describe_drop(result: Quarantine) -> str:
    """One honest sentence about what the gate removed, or an empty string."""
    if not result.dropped:
        return ""
    return f"{result.removed} punto(s) descartado(s) — {result.reasons()}"


def quarantine(key: str, rows: list[tuple[str, float]]) -> Quarantine:
    """Remove non-finite values and values outside the metric's sane band."""
    kept: list[tuple[str, float]] = []
    dropped: list[tuple[str, float, str]] = []
    for day, raw_value in rows:
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            dropped.append((str(day), float("nan"), "valor no numérico"))
            continue
        if not isfinite(value):
            dropped.append((str(day), value, "valor no finito"))
            continue
        if not within_band(key, value):
            spec = fact(key)
            label = f"{spec.low:g}–{spec.high:g} {spec.unit}" if spec else "rango conocido"
            dropped.append((str(day), value, f"fuera del rango normal ({label})"))
            continue
        kept.append((str(day), value))
    return Quarantine(kept, dropped)
