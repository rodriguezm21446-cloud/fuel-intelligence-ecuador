"""Measured financial mathematics for the market layer.

Pure, deterministic and descriptive: every function returns a value or ``None``,
and none of them predicts a direction.  The caller decides whether the sample is
large enough to publish.  The matching definitions live in ``knowledge.py`` so
the bot can explain what it computed and why, never as a causal claim.
"""

from __future__ import annotations

from math import isfinite, log, sqrt
from statistics import median

TRADING_DAYS = 252


def _finite(value: float) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def log_returns(rows: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Daily logarithmic returns, the additive basis for volatility and beta."""
    output: list[tuple[str, float]] = []
    for (_, previous_raw), (day, current_raw) in zip(rows, rows[1:]):
        previous, current = _finite(previous_raw), _finite(current_raw)
        if previous is None or current is None or previous <= 0 or current <= 0:
            continue
        output.append((str(day), log(current / previous)))
    return output


def realized_volatility(
    rows: list[tuple[str, float]], window: int = 20, periods_per_year: int = TRADING_DAYS
) -> float | None:
    """Annualised standard deviation of the last ``window`` daily returns, in %."""
    returns = [value for _, value in log_returns(rows)]
    if window < 2 or len(returns) < window:
        return None
    return _annualize(returns[-window:], periods_per_year)


def volatility_series(
    rows: list[tuple[str, float]], window: int = 20, periods_per_year: int = TRADING_DAYS
) -> list[tuple[str, float]]:
    """Rolling annualised volatility, so the current level can be ranked."""
    returns = log_returns(rows)
    output: list[tuple[str, float]] = []
    for index in range(window - 1, len(returns)):
        sample = [value for _, value in returns[index - window + 1:index + 1]]
        output.append((returns[index][0], _annualize(sample, periods_per_year)))
    return output


def _annualize(sample: list[float], periods_per_year: int) -> float:
    center = sum(sample) / len(sample)
    variance = sum((value - center) ** 2 for value in sample) / (len(sample) - 1)
    return sqrt(variance) * sqrt(periods_per_year) * 100


def percentile_rank(rows: list[tuple[str, float]], value: float | None = None) -> int | None:
    """Where the latest (or given) value sits inside the observed history, 0–100."""
    values = [number for _, raw in rows if (number := _finite(raw)) is not None]
    if len(values) < 20:
        return None
    target = values[-1] if value is None else value
    below = sum(1 for item in values if item <= target)
    return round(below / len(values) * 100)


def moving_average(rows: list[tuple[str, float]], window: int = 20) -> float | None:
    values = [number for _, raw in rows[-window:] if (number := _finite(raw)) is not None]
    if window < 1 or len(values) < window:
        return None
    return sum(values) / len(values)


def level_zscore(rows: list[tuple[str, float]], window: int = 60) -> float | None:
    """Robust median/MAD score of the latest level against the prior window."""
    values = [number for _, raw in rows if (number := _finite(raw)) is not None]
    if len(values) < 20:
        return None
    history = values[-(window + 1):-1]
    center = median(history)
    mad = median(abs(item - center) for item in history)
    if mad <= 1e-12:
        return None
    return 0.67448975 * (values[-1] - center) / mad


def _paired_returns(
    first: list[tuple[str, float]], second: list[tuple[str, float]], window: int
) -> tuple[list[float], list[float]]:
    a, b = dict(first), dict(second)
    days = sorted(a.keys() & b.keys())
    if window and len(days) > window:
        days = days[-window:]
    first_returns: list[float] = []
    second_returns: list[float] = []
    for previous, current in zip(days, days[1:]):
        if a[previous] > 0 and b[previous] > 0:
            first_returns.append(log(a[current] / a[previous]))
            second_returns.append(log(b[current] / b[previous]))
    return first_returns, second_returns


def return_correlation(
    first: list[tuple[str, float]], second: list[tuple[str, float]], window: int = 60
) -> float | None:
    """Pearson correlation of aligned daily returns; measures co-movement, not cause."""
    a, b = _paired_returns(first, second, window)
    if len(a) < 20:
        return None
    mean_a, mean_b = sum(a) / len(a), sum(b) / len(b)
    covariance = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    variance_a = sum((x - mean_a) ** 2 for x in a)
    variance_b = sum((y - mean_b) ** 2 for y in b)
    if variance_a <= 0 or variance_b <= 0:
        return None
    return round(covariance / sqrt(variance_a * variance_b), 2)


def beta(
    asset: list[tuple[str, float]], benchmark: list[tuple[str, float]], window: int = 60
) -> float | None:
    """Sensitivity of the asset's returns to the benchmark's; descriptive only."""
    a, b = _paired_returns(asset, benchmark, window)
    if len(a) < 20:
        return None
    mean_a, mean_b = sum(a) / len(a), sum(b) / len(b)
    covariance = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    variance_b = sum((y - mean_b) ** 2 for y in b)
    if variance_b <= 0:
        return None
    return round(covariance / variance_b, 2)


def max_drawdown(rows: list[tuple[str, float]]) -> float | None:
    """Largest peak-to-trough fall over the series, as a negative percentage."""
    values = [number for _, raw in rows if (number := _finite(raw)) is not None]
    if len(values) < 2:
        return None
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1)
    return round(worst * 100, 2)
