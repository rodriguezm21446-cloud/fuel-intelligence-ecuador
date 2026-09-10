from __future__ import annotations

from datetime import date, datetime, timedelta
import math
from pathlib import Path

from PIL import Image, ImageDraw

from .charts import _text as _base_text, choose_chart_kind


BG = "#07090d"
PANEL = "#0d1218"
GRID = "#29323d"
INK = "#f8fafc"
MUTED = "#aab4c0"
AMBER = "#ff9d00"
BLUE = "#46a6ff"
GREEN = "#19c37d"
RED = "#ff5d68"


def _text(draw, xy, value, size, color=INK, bold=False, anchor=None):
    _base_text(draw, xy, value, size, color, bold, anchor, mono=True)


def stochastic_rsi(values: list[float], length: int = 14) -> list[float | None]:
    gains = [max(values[i] - values[i - 1], 0) for i in range(1, len(values))]
    losses = [max(values[i - 1] - values[i], 0) for i in range(1, len(values))]
    rsi: list[float | None] = [None] * len(values)
    for i in range(length, len(values)):
        gain = sum(gains[i - length:i]) / length
        loss = sum(losses[i - length:i]) / length
        rsi[i] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    result: list[float | None] = [None] * len(values)
    for i in range(length * 2 - 1, len(values)):
        window = [x for x in rsi[i - length + 1:i + 1] if x is not None]
        if len(window) == length:
            low, high = min(window), max(window)
            result[i] = 50.0 if high == low else (rsi[i] - low) / (high - low) * 100
    return result


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
    """Use the last verified close of each ISO week without smoothing values."""
    weeks: dict[tuple[int, int], tuple[str, float]] = {}
    for day, value in sorted(rows):
        parsed = date.fromisoformat(day)
        iso = parsed.isocalendar()
        weeks[(iso.year, iso.week)] = (day, value)
    return list(weeks.values())


def generate_openbb_market_chart(destination: Path) -> Path | None:
    series = {}
    for name, symbols in {"WTI": ("CL", "CL=F"), "BRENT": ("BZ", "BZ=F")}.items():
        for symbol in symbols:
            try:
                rows = _history(symbol)
                if len(rows) >= 30:
                    series[name] = rows
                    break
            except Exception:
                continue
    if not series:
        return None

    raw_series = series
    series = {name: _weekly_closes(rows) for name, rows in raw_series.items()}

    image = Image.new("RGB", (1600, 900), BG)
    draw = ImageDraw.Draw(image)
    _text(draw, (55, 30), "PETRÓLEO: WTI Y BRENT", 31, INK, True)
    _text(draw, (55, 73), "Seis meses · último cierre semanal real, sin suavizado artificial", 15, MUTED)
    left, right, top, bottom = 105, 1515, 190, 665
    draw.rectangle((left, top, right, bottom), fill=PANEL, outline=GRID)
    values = [v for rows in series.values() for _, v in rows]
    observed_low, observed_high = min(values), max(values)
    padding = max((observed_high - observed_low) * 0.08, 1.0)
    low, high = observed_low - padding, observed_high + padding
    span = high - low
    first = min(rows[0][0] for rows in series.values())
    last = max(rows[-1][0] for rows in series.values())
    first_day, last_day = date.fromisoformat(first), date.fromisoformat(last)
    total_days = max(1, (last_day - first_day).days)

    def x_pos(day: str) -> float:
        return left + (date.fromisoformat(day) - first_day).days / total_days * (right - left)

    def y_pos(value: float) -> float:
        return bottom - (value - low) / span * (bottom - top)

    for step in range(6):
        y = top + (bottom - top) * step / 5
        draw.line((left, y, right, y), fill=GRID)
        _text(draw, (left - 14, y), f"${high - span * step / 5:.0f}", 11, MUTED, True, "rm")
    for index, (name, rows) in enumerate(series.items()):
        color = AMBER if name == "WTI" else BLUE
        points = [(x_pos(day), y_pos(value)) for day, value in rows]
        if choose_chart_kind("market_price", len(points), has_ohlc=False) == "line":
            draw.line(points, fill=color, width=4, joint="curve")
        latest_rows = raw_series[name]
        daily = latest_rows[-1][1] - latest_rows[-2][1]
        sign = "+" if daily > 0 else ""
        daily_color = GREEN if daily > 0 else RED if daily < 0 else MUTED
        card_x = 80 + index * 390
        _text(draw, (card_x, 120), name, 16, color, True)
        _text(draw, (card_x + 70, 120), f"${latest_rows[-1][1]:.2f}", 23, INK, True)
        _text(draw, (card_x + 200, 122), f"{sign}${daily:.2f} último día", 13, daily_color, True)
        end_x, end_y = points[-1]
        draw.ellipse((end_x - 5, end_y - 5, end_x + 5, end_y + 5), fill=color, outline=INK, width=2)
        _text(draw, (end_x - 10, end_y - 20), f"{name} ${latest_rows[-1][1]:.2f}", 12, color, True, "ra")

    for step in range(6):
        tick_day = first_day + timedelta(days=round(total_days * step / 5))
        x = left + (tick_day - first_day).days / total_days * (right - left)
        draw.line((x, bottom, x, bottom + 8), fill=MUTED)
        _text(draw, (x, bottom + 18), f"{tick_day:%d/%m/%y}", 11, MUTED, anchor="ma")
    stats = " | ".join(
        f"{name}: 6 meses {(rows[-1][1] / rows[0][1] - 1) * 100:+.1f}% · máx ${max(v for _, v in rows):.2f} · mín ${min(v for _, v in rows):.2f}"
        for name, rows in raw_series.items()
    )
    spread_text = ""
    if "WTI" in series and "BRENT" in series:
        spread = raw_series["BRENT"][-1][1] - raw_series["WTI"][-1][1]
        spread_text = f"Diferencia Brent–WTI: ${spread:+.2f} por barril"
    _text(draw, (80, 735), "LECTURA CLAVE", 14, AMBER, True)
    _text(draw, (80, 770), spread_text or "Solo hay una referencia disponible; no se calcula diferencia.", 18, INK, True)
    _text(draw, (80, 810), stats, 12, INK)
    _text(draw, (80, 852), "FUENTE: OPENBB PLATFORM · PROVEEDOR: YAHOO FINANCE", 13, INK, True)
    _text(draw, (80, 878), f"Cierres semanales · Periodo {first} a {last} · USD por barril · No son precios de gasolina", 11, INK, True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, "PNG", optimize=True)
    return destination

