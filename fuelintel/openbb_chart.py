from __future__ import annotations

from datetime import date, timedelta
import math
from pathlib import Path

from PIL import Image, ImageDraw

from .charts import AMBER, BG, BLUE, GREEN, GRID, MUTED, PANEL, RED, WHITE, _text


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

    image = Image.new("RGB", (1600, 900), BG)
    draw = ImageDraw.Draw(image)
    _text(draw, (55, 30), "PETRÓLEO: WTI Y BRENT", 31, WHITE, True)
    _text(draw, (55, 73), "Evolución de los precios de referencia durante los últimos seis meses", 15, MUTED)
    left, right, top, bottom = 80, 1515, 190, 680
    draw.rectangle((left, top, right, bottom), fill=PANEL, outline=GRID)
    values = [v for rows in series.values() for _, v in rows]
    low, high = min(values), max(values)
    span = max(high - low, 1)
    max_len = max(len(rows) for rows in series.values())
    for step in range(6):
        y = top + (bottom - top) * step / 5
        draw.line((left, y, right, y), fill=GRID)
        _text(draw, (right - 8, y - 17), f"${high - span * step / 5:.2f}", 10, MUTED, anchor="ra")
    for index, (name, rows) in enumerate(series.items()):
        color = AMBER if name == "WTI" else BLUE
        points = [(left + i * (right - left) / max(max_len - 1, 1), bottom - (v - low) / span * (bottom - top)) for i, (_, v) in enumerate(rows)]
        draw.line(points, fill=color, width=3)
        daily = rows[-1][1] - rows[-2][1]
        sign = "+" if daily > 0 else ""
        daily_color = GREEN if daily > 0 else RED if daily < 0 else MUTED
        card_x = 80 + index * 390
        _text(draw, (card_x, 120), name, 16, color, True)
        _text(draw, (card_x + 70, 120), f"${rows[-1][1]:.2f}", 23, WHITE, True)
        _text(draw, (card_x + 200, 122), f"{sign}${daily:.2f} último día", 13, daily_color, True)
        end_x, end_y = points[-1]
        _text(draw, (end_x - 10, end_y - 20), f"{name} ${rows[-1][1]:.2f}", 12, color, True, "ra")

    longest = max(series.values(), key=len)
    for step in range(6):
        index = round((len(longest) - 1) * step / 5)
        x = left + index * (right - left) / max(max_len - 1, 1)
        draw.line((x, bottom, x, bottom + 8), fill=MUTED)
        _text(draw, (x, bottom + 18), longest[index][0], 10, MUTED, anchor="ma")
    first = min(rows[0][0] for rows in series.values())
    last = max(rows[-1][0] for rows in series.values())
    stats = " | ".join(
        f"{name}: último ${rows[-1][1]:.2f} · máx ${max(v for _, v in rows):.2f} · mín ${min(v for _, v in rows):.2f}"
        for name, rows in series.items()
    )
    spread_text = ""
    if "WTI" in series and "BRENT" in series:
        spread = series["BRENT"][-1][1] - series["WTI"][-1][1]
        spread_text = f"Diferencia Brent–WTI: ${spread:+.2f} por barril"
    _text(draw, (80, 755), "LECTURA RÁPIDA", 14, AMBER, True)
    _text(draw, (80, 787), spread_text or "Solo hay una referencia disponible; no se calcula diferencia.", 17, WHITE, True)
    _text(draw, (80, 825), stats, 12, WHITE)
    _text(draw, (80, 864), f"Fuente: OpenBB Platform / Yahoo Finance · Cierres diarios · Periodo: {first} a {last} · USD por barril", 11, MUTED)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, "PNG", optimize=True)
    return destination

