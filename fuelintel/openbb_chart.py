from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
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
                "min": f"{min(raw_values):.2f}",
                "max": f"{max(raw_values):.2f}",
            }
        )
    insight = "Solo hay una referencia disponible; no se calcula el diferencial."
    if "WTI" in raw_series and "Brent" in raw_series:
        spread = raw_series["Brent"][-1][1] - raw_series["WTI"][-1][1]
        insight = f"Diferencial Brent–WTI: ${spread:+.2f} por barril. No implica por sí solo un cambio en Ecuador."
    verified = datetime.now(timezone.utc).astimezone(ZoneInfo("America/Guayaquil"))
    return {
        "title": "Petróleo: WTI y Brent",
        "subtitle": "Últimos seis meses · cierres semanales reales, sin suavizado",
        "labels": [datetime.fromisoformat(day).strftime("%d/%m/%Y") for day in labels],
        "datasets": datasets,
        "annotations": [],
        "references": references,
        "stepped": False,
        "decimals": 2,
        "insight": insight,
        "source": f"Fuente: OpenBB Platform · Proveedor: Yahoo Finance · Verificado: {verified:%d/%m/%Y %H:%M} ECT",
        "credit": own_data_label(),
    }


def generate_openbb_market_chart(destination: Path) -> Path | None:
    raw_series: dict[str, list[tuple[str, float]]] = {}
    for name, symbols in {"WTI": ("CL", "CL=F"), "Brent": ("BZ", "BZ=F")}.items():
        for symbol in symbols:
            try:
                rows = _history(symbol)
                if len(rows) >= 30:
                    raw_series[name] = rows
                    break
            except Exception:
                continue
    payload = build_market_payload(raw_series)
    return render_chart(payload, destination) if payload else None

