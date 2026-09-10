from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
import os
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageFont

from .calculations import price_change
from .storage import Store


WIDTH, HEIGHT = 1600, 900
# A light editorial palette stays legible on Telegram and when reposted to X.
BG = "#f4f6f8"
PANEL = "#ffffff"
GRID = "#d9dee5"
MUTED = "#667085"
WHITE = "#101828"  # Kept as a compatibility name: this is the primary ink.
HEADER = "#101828"
HEADER_TEXT = "#ffffff"
AMBER = "#f79009"
GREEN = "#079455"
RED = "#d92d20"
BLUE = "#1570ef"
PURPLE = "#7f56d9"

COLORS = {
    "Extra": BLUE,
    "Ecopaís": GREEN,
    "Súper": AMBER,
    "Diésel Premium": PURPLE,
}


def _color(product: str) -> str:
    for name, color in COLORS.items():
        if product.startswith(name):
            return color
    return WHITE


def _font(size: int, bold: bool = False, mono: bool = False):
    if mono:
        names = (
            [
                "C:/Windows/Fonts/consolab.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
                "DejaVuSansMono-Bold.ttf",
            ]
            if bold
            else [
                "C:/Windows/Fonts/consola.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
                "DejaVuSansMono.ttf",
            ]
        )
    else:
        names = (
            [
                "C:/Windows/Fonts/arialbd.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "DejaVuSans-Bold.ttf",
            ]
            if bold
            else [
                "C:/Windows/Fonts/arial.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "DejaVuSans.ttf",
            ]
        )
    for name in names:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _money(value: Decimal) -> str:
    return f"${value:.3f}"


def _parse_x(row) -> datetime:
    raw = row["effective_from"] or row["observed_at"]
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _text(
    draw: ImageDraw.ImageDraw,
    xy,
    value: str,
    size: int,
    color=WHITE,
    bold: bool = False,
    anchor=None,
    mono: bool = False,
):
    draw.text(xy, value, font=_font(size, bold, mono), fill=color, anchor=anchor)


def choose_chart_kind(dataset: str, points: int, has_ohlc: bool = False) -> str:
    """Choose the simplest truthful visual for the supplied data."""
    if points < 2:
        return "card"
    if dataset == "official_period_price":
        return "step"
    if dataset in {"change", "ranking", "comparison"}:
        return "bar"
    if has_ohlc and dataset == "market_price":
        return "candlestick"
    return "line"


def brand_name() -> str:
    raw = os.getenv("FUELINTEL_BRAND_NAME", "FUEL INTELLIGENCE ECUADOR")
    clean = " ".join(raw.split()).upper()[:48]
    return clean or "FUEL INTELLIGENCE ECUADOR"


def own_data_label(estimated: bool = False) -> str:
    if estimated:
        return f"ESTIMACIÓN PROPIA — {brand_name()} — NO ES DATO OFICIAL"
    return f"CÁLCULOS Y VISUALIZACIÓN: {brand_name()}"


def generate_dashboard(store: Store, destination: Path, now_local: datetime, demo: bool = False) -> Path | None:
    rows = store.recent_prices()
    if not rows:
        return None

    consumer_rows = [row for row in rows if "terminal automotriz" not in row["product"].lower()]
    terminal_only = not consumer_rows
    rows = rows if terminal_only else consumer_rows

    grouped: dict[str, list] = defaultdict(list)
    for row in rows:
        grouped[row["product"]].append(row)
    for product in grouped:
        grouped[product].sort(key=_parse_x)
    data_updated_at = max(_parse_x(row) for row in rows)

    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)

    draw.rectangle((0, 0, WIDTH, 88), fill=HEADER)
    draw.rectangle((0, 0, 9, 88), fill=AMBER)
    _text(draw, (34, 22), "FUEL INTELLIGENCE", 28, AMBER, True)
    _text(draw, (34, 56), "ECUADOR // COMBUSTIBLES Y MERCADOS", 12, MUTED, True)
    _text(draw, (WIDTH - 32, 31), f"DATOS AL {data_updated_at:%d/%m/%Y  %H:%M}", 15, HEADER_TEXT, True, "ra")
    _text(draw, (WIDTH - 32, 57), "DATOS VERIFICADOS · USD/GALÓN", 11, "#cbd5e1", False, "ra")
    if demo:
        draw.rounded_rectangle((660, 24, 1010, 64), radius=7, fill="#33141a", outline=RED, width=2)
        _text(draw, (835, 44), "VISTA PREVIA · DATOS DE PRUEBA", 13, RED, True, "mm")

    chart_box = (28, 116, 1120, 675)
    draw.rounded_rectangle(chart_box, radius=10, fill=PANEL, outline="#1d2730", width=2)
    title = "PRECIOS OFICIALES EN TERMINAL" if terminal_only else "PRECIOS AL CONSUMIDOR EN ECUADOR"
    subtitle = "NO ES PRECIO FINAL DE GASOLINERA" if terminal_only else "USD por galón · comparación por periodo de vigencia"
    _text(draw, (54, 140), title, 17, WHITE, True)
    _text(draw, (54, 166), subtitle, 12, RED if terminal_only else MUTED, terminal_only)

    plot_left, plot_top, plot_right, plot_bottom = 86, 208, 1082, 600
    all_values = [float(Decimal(row["price"])) for product_rows in grouped.values() for row in product_rows]
    low, high = min(all_values), max(all_values)
    spread = max(0.10, high - low)
    low = max(0.0, low - spread * 0.14)
    high += spread * 0.14
    all_dates = [_parse_x(row) for product_rows in grouped.values() for row in product_rows]
    first_date, last_date = min(all_dates), max(all_dates)
    total_seconds = max(1.0, (last_date - first_date).total_seconds())

    for step in range(6):
        y = plot_top + step * (plot_bottom - plot_top) / 5
        draw.line((plot_left, y, plot_right, y), fill=GRID, width=1)
        value = high - step * (high - low) / 5
        _text(draw, (plot_left - 12, y), f"${value:.3f}", 10, MUTED, anchor="rm")

    def x_pos(value: datetime) -> float:
        return plot_left + ((value - first_date).total_seconds() / total_seconds) * (plot_right - plot_left)

    def y_pos(value: float) -> float:
        return plot_bottom - ((value - low) / (high - low)) * (plot_bottom - plot_top)

    for product, product_rows in sorted(grouped.items()):
        color = _color(product)
        points = [(x_pos(_parse_x(row)), y_pos(float(Decimal(row["price"])))) for row in product_rows]
        if choose_chart_kind("official_period_price", len(points)) == "card":
            x, y = points[0]
            draw.line((plot_left, y, plot_right, y), fill=color, width=3)
        else:
            # Official Ecuador prices remain fixed for a validity period, so a
            # staircase is more accurate than a diagonal interpolation.
            step_points = [points[0]]
            for previous, current in zip(points, points[1:]):
                step_points.extend([(current[0], previous[1]), current])
            draw.line(step_points, fill=color, width=4)
        for x, y in points:
            draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=color, outline=BG, width=2)

    for step in range(5):
        when = first_date + (last_date - first_date) * step / 4
        x = x_pos(when)
        draw.line((x, plot_bottom, x, plot_bottom + 8), fill=MUTED)
        _text(draw, (x, plot_bottom + 15), f"{when:%d/%m/%y}", 10, MUTED, anchor="ma")

    legend_x = 54
    for product in sorted(grouped):
        color = _color(product)
        draw.rounded_rectangle((legend_x, 642, legend_x + 16, 658), radius=3, fill=color)
        _text(draw, (legend_x + 24, 650), product, 11, WHITE, True, "lm")
        legend_x += 56 + int(draw.textlength(product, font=_font(11, True)))

    side_x = 1148
    _text(draw, (side_x, 121), "ÚLTIMOS VALORES", 15, MUTED, True)
    card_y = 154
    for product, product_rows in sorted(grouped.items()):
        current = Decimal(product_rows[-1]["price"])
        previous = Decimal(product_rows[-2]["price"]) if len(product_rows) > 1 else None
        change = price_change(current, previous)
        color = _color(product)
        draw.rounded_rectangle((side_x, card_y, 1572, card_y + 105), radius=9, fill=PANEL, outline="#1d2730", width=2)
        draw.rectangle((side_x, card_y, side_x + 7, card_y + 105), fill=color)
        _text(draw, (side_x + 24, card_y + 18), product.upper(), 13, MUTED, True)
        _text(draw, (side_x + 24, card_y + 48), _money(current), 27, WHITE, True)
        if change.absolute is None:
            delta_text, delta_color = "SIN HISTORIAL", MUTED
        else:
            delta_text = f"{change.absolute:+.3f}  /  {change.percent:+.2f}%"
            delta_color = GREEN if change.absolute > 0 else RED if change.absolute < 0 else MUTED
        _text(draw, (1548, card_y + 61), delta_text, 12, delta_color, True, "ra")
        _text(draw, (1548, card_y + 84), (product_rows[-1]["effective_from"] or "periodo no informado"), 10, MUTED, False, "ra")
        card_y += 119

    footer_y = 711
    draw.rounded_rectangle((28, footer_y, 1572, 856), radius=10, fill=PANEL, outline="#1d2730", width=2)
    _text(draw, (52, footer_y + 22), "CÓMO LEER ESTE GRÁFICO", 13, AMBER, True)
    _text(
        draw,
        (52, footer_y + 57),
        "Cada escalón representa un nuevo periodo oficial; una línea plana significa que el precio no cambió.",
        18,
        WHITE,
        True,
    )
    newest = max(rows, key=_parse_x)
    source_url = newest["source_url"]
    source_name = urlparse(source_url).netloc.removeprefix("www.") or "documento oficial"
    period = newest["effective_from"] or "fecha de observación"
    _text(draw, (52, footer_y + 96), f"FUENTE OFICIAL: {source_name} · PERIODO: {period} · USD POR GALÓN", 11, WHITE, True, mono=True)
    _text(draw, (1548, footer_y + 96), own_data_label(), 11, AMBER, True, "ra", mono=True)

    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, "PNG", optimize=True)
    return destination

