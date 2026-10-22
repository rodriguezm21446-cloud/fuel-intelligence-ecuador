from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from math import isfinite


SIX_DECIMALS = Decimal("0.000001")


class DataIntegrityError(ValueError):
    """Raised before a mathematically inconsistent chart can be published."""


def percentage_change(current: float | Decimal, previous: float | Decimal) -> float:
    """Return a reproducible percentage change using decimal arithmetic."""
    try:
        current_value = Decimal(str(current))
        previous_value = Decimal(str(previous))
    except InvalidOperation as error:
        raise DataIntegrityError("valor de mercado no numérico") from error
    if not current_value.is_finite() or not previous_value.is_finite():
        raise DataIntegrityError("valor de mercado no finito")
    if current_value <= 0 or previous_value <= 0:
        raise DataIntegrityError("el cálculo porcentual requiere precios positivos")
    result = ((current_value / previous_value) - Decimal(1)) * Decimal(100)
    return float(result.quantize(SIX_DECIMALS, rounding=ROUND_HALF_UP))


def validate_market_series(name: str, rows: list[tuple[str, float]]) -> None:
    """Check ordering, duplicated dates and numeric values before analysis."""
    previous_day: date | None = None
    seen: set[date] = set()
    for raw_day, raw_value in rows:
        try:
            day = date.fromisoformat(str(raw_day))
            value = float(raw_value)
        except (TypeError, ValueError) as error:
            raise DataIntegrityError(f"{name}: fecha o precio inválido") from error
        if day in seen:
            raise DataIntegrityError(f"{name}: fecha duplicada {day.isoformat()}")
        if previous_day is not None and day <= previous_day:
            raise DataIntegrityError(f"{name}: fechas fuera de orden")
        if not isfinite(value) or value <= 0:
            raise DataIntegrityError(f"{name}: precio no positivo o no finito")
        seen.add(day)
        previous_day = day


def validate_chart_payload(payload: dict, *, heatmap: bool = False) -> None:
    """Circuit breaker shared by every renderer.

    It prevents mismatched labels, NaN/Infinity, impossible historical ranges
    and inconsistent heatmap matrices from reaching Telegram.
    """
    if not isinstance(payload, dict):
        raise DataIntegrityError("el gráfico no tiene una carga válida")
    if not str(payload.get("title", "")).strip():
        raise DataIntegrityError("el gráfico no tiene título")

    if heatmap:
        rows = payload.get("rows")
        if not isinstance(rows, list) or not rows:
            raise DataIntegrityError("el mapa de calor no contiene filas")
        expected_width: int | None = None
        for row in rows:
            values = row.get("values", []) if isinstance(row, dict) else []
            if expected_width is None:
                expected_width = len(values)
            if not values or len(values) != expected_width:
                raise DataIntegrityError("el mapa de calor tiene columnas inconsistentes")
            for value in values:
                if value is not None and not isfinite(float(value)):
                    raise DataIntegrityError("el mapa de calor contiene un valor no finito")
        return

    labels = payload.get("labels")
    datasets = payload.get("datasets")
    if not isinstance(labels, list) or not labels:
        raise DataIntegrityError("el gráfico no contiene fechas o categorías")
    if not isinstance(datasets, list) or not datasets:
        raise DataIntegrityError("el gráfico no contiene series")
    decimals = payload.get("displayDecimals", 3)
    if not isinstance(decimals, int) or not 0 <= decimals <= 6:
        raise DataIntegrityError("precisión decimal fuera de rango")

    context = payload.get("context", []) or []
    if not isinstance(context, list) or len(context) > 3:
        raise DataIntegrityError("el bloque de contexto no es una lista corta")
    for line in context:
        if not isinstance(line, str) or not line.strip() or len(line) > 220:
            raise DataIntegrityError("línea de contexto vacía o demasiado larga")

    labels_count = len(labels)
    series_labels: set[str] = set()
    for dataset in datasets:
        label = str(dataset.get("label", "")).strip()
        values = dataset.get("values")
        if not label or label in series_labels:
            raise DataIntegrityError("serie sin nombre o repetida")
        if not isinstance(values, list) or len(values) != labels_count:
            raise DataIntegrityError(f"{label}: cantidad de valores distinta del eje X")
        for value in values:
            if value is not None and not isfinite(float(value)):
                raise DataIntegrityError(f"{label}: valor no finito")
        series_labels.add(label)

    references = payload.get("references", []) or []
    reference_labels = {str(item.get("label", "")) for item in references}
    if references and reference_labels != series_labels:
        raise DataIntegrityError("el rango histórico no cubre todas las series")
    for reference in references:
        low, high = float(reference["min"]), float(reference["max"])
        if not isfinite(low) or not isfinite(high) or low > high:
            raise DataIntegrityError("rango histórico inválido")

    for annotation in payload.get("annotations", []) or []:
        if annotation.get("axis") == "y":
            value = float(annotation.get("value"))
            if not isfinite(value):
                raise DataIntegrityError("anotación Y inválida")
        else:
            index = int(annotation.get("index", -1))
            if index < 0 or index >= labels_count:
                raise DataIntegrityError("anotación fuera del eje X")

    series_count = len(datasets)
    for marker in payload.get("minimums", []) or []:
        dataset_index = int(marker.get("datasetIndex", -1))
        index = int(marker.get("index", -1))
        if not str(marker.get("text", "")).strip():
            raise DataIntegrityError("marcador de mínimo sin texto")
        if not 0 <= dataset_index < series_count:
            raise DataIntegrityError("marcador de mínimo fuera de las series")
        if index < 0 or index >= labels_count:
            raise DataIntegrityError("marcador de mínimo fuera del eje X")
        value = datasets[dataset_index]["values"][index]
        if value is None or not isfinite(float(value)):
            raise DataIntegrityError("marcador de mínimo sin valor")
        present = [float(item) for item in datasets[dataset_index]["values"] if item is not None]
        if present and abs(float(value) - min(present)) > 1e-9:
            raise DataIntegrityError("el marcador no apunta al mínimo real de la serie")
