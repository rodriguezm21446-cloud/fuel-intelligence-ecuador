from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from PIL import Image, ImageDraw

from .charts import AMBER, BG, BLUE, GRID, MUTED, PANEL, RED, WHITE, _text


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
    rows = []
    for _, row in frame.iterrows():
        when = row.get("date", row.iloc[0])
        close = row.get("close")
        if close is not None:
            rows.append((str(when)[:10], float(close)))
    return rows


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
    _text(draw, (55, 30), "OPENBB // MERCADO DE PETRÓLEO", 27, WHITE, True)
    _text(draw, (55, 67), "WTI Y BRENT · CIERRE DIARIO · STOCHASTIC RSI (14)", 13, MUTED)
    left, right, top, bottom = 80, 1535, 125, 535
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
        _text(draw, (90 + index * 180, 100), f"{name} ${rows[-1][1]:.2f}", 13, color, True)

    osc_top, osc_bottom = 610, 840
    draw.rectangle((left, osc_top, right, osc_bottom), fill=PANEL, outline=GRID)
    for level, color in ((80, RED), (20, BLUE)):
        y = osc_bottom - level / 100 * (osc_bottom - osc_top)
        draw.line((left, y, right, y), fill=color, width=2)
        _text(draw, (right - 8, y - 16), str(level), 10, color, anchor="ra")
    for index, (name, rows) in enumerate(series.items()):
        color = AMBER if name == "WTI" else BLUE
        indicator = stochastic_rsi([value for _, value in rows])
        points = [(left + i * (right - left) / max(max_len - 1, 1), osc_bottom - value / 100 * (osc_bottom - osc_top)) for i, value in enumerate(indicator) if value is not None]
        if len(points) > 1:
            draw.line(points, fill=color, width=2)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, "PNG", optimize=True)
    return destination

