from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from math import ceil
from statistics import median


CENT = Decimal("0.01")
THOUSANDTH = Decimal("0.001")


@dataclass(frozen=True, slots=True)
class ForecastResult:
    product: str
    publishable: bool
    reason: str
    observations: int
    estimate: Decimal | None = None
    low: Decimal | None = None
    high: Decimal | None = None
    probability_up: int | None = None
    probability_flat: int | None = None
    probability_down: int | None = None
    model_mae: Decimal | None = None
    naive_mae: Decimal | None = None
    target_effective_from: date | None = None


def _q(value: Decimal, places: Decimal = THOUSANDTH) -> Decimal:
    return value.quantize(places, rounding=ROUND_HALF_UP)


def _median(values: list[Decimal]) -> Decimal:
    return Decimal(str(median(values)))


def _mae(errors: list[Decimal]) -> Decimal:
    return sum((abs(value) for value in errors), Decimal("0")) / Decimal(len(errors))


def _history_rows(rows) -> list[tuple[date, Decimal]]:
    """Normalize and collapse duplicate effective dates before modelling."""
    by_date: dict[date, Decimal] = {}
    for row in rows:
        raw_date = row["effective_from"] or str(row["observed_at"])[:10]
        by_date[date.fromisoformat(raw_date)] = Decimal(str(row["price"]))
    return sorted(by_date.items())


def forecast_next_price(product: str, rows, *, min_observations: int = 10) -> ForecastResult:
    """Build an auditable one-period forecast, or refuse to publish it.

    The candidate model is a rolling median of recent changes. It is published
    only if a walk-forward test beats the no-change baseline by at least 10%.
    """
    history = _history_rows(rows)
    count = len(history)
    if count < min_observations:
        return ForecastResult(
            product, False,
            f"historial insuficiente: {count}/{min_observations} periodos confirmados",
            count,
        )

    prices = [price for _, price in history]
    changes = [prices[index] - prices[index - 1] for index in range(1, count)]
    model_errors: list[Decimal] = []
    naive_errors: list[Decimal] = []
    for target in range(5, count):
        available = [prices[index] - prices[index - 1] for index in range(1, target)]
        predicted = prices[target - 1] + _median(available[-6:])
        model_errors.append(prices[target] - predicted)
        naive_errors.append(prices[target] - prices[target - 1])

    if len(model_errors) < 4:
        return ForecastResult(product, False, "faltan ventanas para una prueba retrospectiva", count)

    model_mae = _q(_mae(model_errors))
    naive_mae = _q(_mae(naive_errors))
    if naive_mae == 0 or model_mae > naive_mae * Decimal("0.90"):
        return ForecastResult(
            product, False,
            "el modelo no mejora al supuesto prudente de precio sin cambio",
            count,
            model_mae=model_mae,
            naive_mae=naive_mae,
        )

    recent_changes = changes[-8:]
    estimate = _q(prices[-1] + _median(recent_changes))
    residual_scale = _median([abs(value) for value in model_errors]) or naive_mae or CENT
    band = max(CENT, residual_scale * Decimal("1.65"))
    low = _q(max(Decimal("0"), estimate - band))
    high = _q(estimate + band)

    up = sum(value > CENT / 2 for value in recent_changes)
    down = sum(value < -(CENT / 2) for value in recent_changes)
    flat = len(recent_changes) - up - down
    denominator = len(recent_changes) + 3
    probability_up = int((Decimal(up + 1) / Decimal(denominator) * 100).quantize(Decimal("1")))
    probability_flat = int((Decimal(flat + 1) / Decimal(denominator) * 100).quantize(Decimal("1")))
    probability_down = 100 - probability_up - probability_flat
    newest_day = history[-1][0]
    newest_end = next(
        (
            row["effective_to"]
            for row in reversed(rows)
            if "effective_to" in row.keys()
            and row["effective_from"] == newest_day.isoformat()
            and row["effective_to"]
        ),
        None,
    )
    if newest_end:
        target_day = date.fromisoformat(newest_end) + timedelta(days=1)
    else:
        gaps = [(history[index][0] - history[index - 1][0]).days for index in range(1, len(history))]
        target_day = newest_day + timedelta(days=max(1, ceil(median(gaps[-6:]))))
    return ForecastResult(
        product=product,
        publishable=True,
        reason="validación retrospectiva superó la referencia sin cambio",
        observations=count,
        estimate=estimate,
        low=low,
        high=high,
        probability_up=probability_up,
        probability_flat=probability_flat,
        probability_down=probability_down,
        model_mae=model_mae,
        naive_mae=naive_mae,
        target_effective_from=target_day,
    )


def format_forecast_message(result: ForecastResult, generated_at: datetime | None = None) -> str:
    if not result.publishable:
        return ""
    generated_at = generated_at or datetime.now(timezone.utc)
    return "\n".join(
        [
            "MODELO MATEMÁTICO — ESCENARIO, NO PRECIO OFICIAL",
            f"{result.product}: estimación central ${result.estimate:.3f}; rango ${result.low:.3f}–${result.high:.3f} por galón.",
            (
                f"Probabilidad histórica: sube {result.probability_up}% · "
                f"sin cambio {result.probability_flat}% · baja {result.probability_down}%."
            ),
            (
                f"Validación: {result.observations} periodos · error medio ${result.model_mae:.3f} "
                f"vs ${result.naive_mae:.3f} del modelo sin cambio."
            ),
            "No incorpora una decisión futura del Gobierno ni sustituye una publicación oficial.",
        ]
    )
