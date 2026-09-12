from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

from .chart_renderer import render_chart
from .charts import own_data_label


def _history(symbol: str, days: int = 180) -> list[tuple[str, float]]:
    from openbb import obb

    start = (date.today() - timedelta(days=days)).isoformat()
    output = obb.derivatives.futures.historical(symbol=symbol, start_date=start, provider="yfinance")
    frame = output.to_df().reset_index()
    by_date: dict[str, float] = {}
    for _, row in frame.iterrows():
        when = row.get("date", row.iloc[0])
        close = row.get("close")
        if close is not None:
            day, value = str(when)[:10], float(close)
            if day <= date.today().isoformat() and math.isfinite(value) and 1 <= value <= 500:
                by_date[day] = value
    return sorted(by_date.items())


def _weekly_closes(rows: list[tuple[str, float]]) -> list[tuple[str, float]]:
    weeks: dict[tuple[int, int], tuple[str, float]] = {}
    for day, value in sorted(rows):
        parsed = date.fromisoformat(day)
        iso = parsed.isocalendar()
        weeks[(iso.year, iso.week)] = (day, value)
    return list(weeks.values())


def _change(rows: list[tuple[str, float]], sessions: int) -> float | None:
    if len(rows) <= sessions or rows[-1][1] <= 0 or rows[-1 - sessions][1] <= 0:
        return None
    return (rows[-1][1] / rows[-1 - sessions][1] - 1) * 100


def build_market_briefing(raw_series: dict[str, list[tuple[str, float]]]) -> str:
    """Transparent directional signal; deliberately not a fake calibrated probability."""
    labels = {
        "WTI": "WTI (USD/barril)",
        "Brent": "Brent (USD/barril)",
        "RBOB": "Gasolina RBOB (USD/galón)",
        "ULSD": "Diésel/ULSD (USD/galón)",
    }
    lines = ["LECTURA CUANTITATIVA — MERCADO DE COMBUSTIBLES", "", "DATOS OBSERVADOS:"]
    signals: list[tuple[float, float]] = []
    weights = {"WTI": 0.10, "Brent": 0.20, "RBOB": 0.35, "ULSD": 0.35}
    for name in ("WTI", "Brent", "RBOB", "ULSD"):
        rows = raw_series.get(name, [])
        if not rows:
            continue
        week = _change(rows, 5)
        month = _change(rows, 20)
        if week is not None:
            signals.append((max(-15.0, min(15.0, week)), weights[name]))
        week_text = "n/d" if week is None else f"{week:+.2f}%"
        month_text = "n/d" if month is None else f"{month:+.2f}%"
        lines.append(f"{labels[name]}: {rows[-1][1]:.2f} · 1 semana {week_text} · 1 mes {month_text}")

    if signals:
        weight_sum = sum(weight for _, weight in signals)
        score = sum(value * weight for value, weight in signals) / weight_sum
        if score >= 2:
            direction = "presión alcista"
        elif score <= -2:
            direction = "presión bajista"
        else:
            direction = "presión mixta o neutral"
        lines.extend(
            [
                "",
                "PREDICCIÓN EXPERIMENTAL:",
                f"El conjunto de referencias muestra {direction} a corto plazo (índice direccional {score:+.2f}).",
                "Esto no es una probabilidad calibrada ni una predicción del precio oficial de Ecuador.",
            ]
        )
    else:
        lines.extend(["", "PREDICCIÓN EXPERIMENTAL:", "Datos insuficientes; no se emite señal."])
    lines.extend(
        [
            "",
            "CAUSA:",
            "No puede inferirse únicamente del gráfico. Se añadirá desde noticias corroboradas y fuentes primarias.",
            "",
            "CANAL HACIA ECUADOR:",
            "Debe confirmarse con derivados, flete, ventana de cálculo y documento oficial de ARCH/Petroecuador.",
            "Hora de Ecuador (UTC−5).",
        ]
    )
    return "\n".join(lines)


def build_market_payload(raw_series: dict[str, list[tuple[str, float]]]) -> dict | None:
    if not raw_series:
        return None
    series = {name: _weekly_closes(rows) for name, rows in raw_series.items() if len(rows) >= 2}
    if not series:
        return None
    labels = sorted({day for rows in series.values() for day, _ in rows})
    colors = {"WTI": "#FF9D00", "Brent": "#E8E8E8"}
    datasets = []
    references = []
    for name, rows in series.items():
        by_date = dict(rows)
        datasets.append(
            {
                "label": name,
                "color": colors.get(name, "#8A8A8A"),
                "values": [by_date.get(day) for day in labels],
                "pending": False,
            }
        )
        raw_values = [value for _, value in raw_series[name]]
        references.append(
            {
                "label": name,
                "color": colors.get(name, "#8A8A8A"),
                "min": min(raw_values),
                "max": max(raw_values),
            }
        )
    insight = "Solo hay una referencia disponible; no se calcula el diferencial."
    if "WTI" in raw_series and "Brent" in raw_series:
        spread = raw_series["Brent"][-1][1] - raw_series["WTI"][-1][1]
        insight = f"Diferencial Brent–WTI: ${spread:+.2f} por barril. No implica por sí solo un cambio en Ecuador."
    verified = datetime.now(timezone.utc).astimezone(ZoneInfo("America/Guayaquil"))
    return {
        "title": "Petróleo: WTI y Brent",
        "subtitle": "Últimos seis meses · cierres semanales observados · USD por barril",
        "labels": [datetime.fromisoformat(day).strftime("%d/%m/%Y") for day in labels],
        "datasets": datasets,
        "annotations": [],
        "references": references,
        "stepped": False,
        "insight": insight,
        "source": f"Fuente: OpenBB Platform · Proveedor: Yahoo Finance · Verificado: {verified:%d/%m/%Y %H:%M} · hora de Ecuador (UTC−5)",
        "credit": own_data_label(),
    }


def generate_openbb_market_chart(destination: Path) -> Path | None:
    chart, _, _ = generate_openbb_market_chart_result(destination)
    return chart


def generate_openbb_market_chart_result(destination: Path) -> tuple[Path | None, str | None, str | None]:
    raw_series: dict[str, list[tuple[str, float]]] = {}
    symbols_by_name = {
        "WTI": ("CL", "CL=F"),
        "Brent": ("BZ", "BZ=F"),
        "RBOB": ("RB", "RB=F"),
        "ULSD": ("HO", "HO=F"),
    }
    for name, symbols in symbols_by_name.items():
        for symbol in symbols:
            try:
                rows = _history(symbol)
                if len(rows) >= 30:
                    raw_series[name] = rows
                    break
            except Exception:
                continue
    payload = build_market_payload({key: value for key, value in raw_series.items() if key in {"WTI", "Brent"}})
    if not payload:
        return None, None, None
    briefing = build_market_briefing(raw_series)
    stable_payload = {
        "labels": payload["labels"],
        "datasets": payload["datasets"],
        "references": payload["references"],
        "insight": payload["insight"],
        "briefing": briefing,
    }
    signature = hashlib.sha256(
        json.dumps(stable_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return render_chart(payload, destination), signature, briefing
