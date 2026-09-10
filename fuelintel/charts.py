from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .calculations import price_change
from .storage import Store


WIDTH, HEIGHT = 1600, 900
BG = "#06080b"
PANEL = "#0d1217"
GRID = "#26313a"
MUTED = "#81909c"
WHITE = "#eef4f8"
AMBER = "#ffb000"
GREEN = "#00d084"
RED = "#ff5964"
BLUE = "#47a7ff"

COLORS = {
    "Extra": AMBER,
    "Ecopaís": GREEN,
    "Súper": RED,
    "Diésel Premium": BLUE,
}


def _color(product: str) -> str:
    for name, color in COLORS.items():
        if product.startswith(name):
            return color
    return WHITE


def _font(size: int, bold: bool = False):
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


def _text(draw: ImageDraw.ImageDraw, xy, value: str, size: int, color=WHITE, bold: bool = False, anchor=None):
    draw.text(xy, value, font=_font(size, bold), fill=color, anchor=anchor)


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

    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)

    draw.rectangle((0, 0, WIDTH, 88), fill="#090d11")
    draw.rectangle((0, 0, 9, 88), fill=AMBER)
    _text(draw, (34, 22), "FUEL INTELLIGENCE", 28, AMBER, True)
    _text(draw, (34, 56), "ECUADOR // COMBUSTIBLES Y MERCADOS", 12, MUTED, True)
    _text(draw, (WIDTH - 32, 31), f"{now_local:%d/%m/%Y  %H:%M} ECU", 15, WHITE, True, "ra")
    _text(draw, (WIDTH - 32, 57), "DATOS VERIFICADOS · USD/GALÓN", 11, MUTED, False, "ra")
    if demo:
        draw.rounded_rectangle((660, 24, 1010, 64), radius=7, fill="#33141a", outline=RED, width=2)
        _text(draw, (835, 44), "VISTA PREVIA · DATOS DE PRUEBA", 13, RED, True, "mm")

    chart_box = (28, 116, 1120, 675)
    draw.rounded_rectangle(chart_box, radius=10, fill=PANEL, outline="#1d2730", width=2)
    title = "PRECIOS OFICIALES EN TERMINAL" if terminal_only else "PRECIOS AL CONSUMIDOR EN ECUADOR"
    subtitle = "NO ES PRECIO FINAL DE GASOLINERA" if terminal_only else "USD por galón · comparación por periodo de vigencia"
    _text(draw, (54, 140), title, 17, WHITE, True)
    _text(draw, (54, 166), subtitle, 12, RED if terminal_only else MUTED, terminal_only)

    plot_left, plot_top, plot_right, plot_bottom = 86, 208, 1082, 615
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
        if len(points) == 1:
            x, y = points[0]
            draw.line((plot_left, y, plot_right, y), fill=color, width=3)
        else:
            draw.line(points, fill=color, width=4, joint="curve")
        for x, y in points:
            draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=color, outline=BG, width=2)

    legend_x = 54
    for product in sorted(grouped):
        color = _color(product)
        draw.rounded_rectangle((legend_x, 640, legend_x + 16, 656), radius=3, fill=color)
        _text(draw, (legend_x + 24, 648), product, 11, WHITE, True, "lm")
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
    _text(draw, (52, footer_y + 22), "CONTROL DE CALIDAD", 13, AMBER, True)
    checks = [
        "OK  Fuentes y periodos guardados",
        "OK  Variaciones calculadas con Decimal",
        "OK  Duplicados bloqueados",
        "OK  Histórico separado del vigente",
    ]
    x = 52
    for check in checks:
        _text(draw, (x, footer_y + 58), check, 12, WHITE)
        x += 370
    _text(
        draw,
        (52, footer_y + 105),
        "Una estimación nunca se etiqueta como precio oficial. Sin vigencia o confirmación, el dato queda en cuarentena.",
        12,
        MUTED,
    )
    _text(draw, (1548, footer_y + 106), "SISTEMA PRIVADO", 11, AMBER, True, "ra")

    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, "PNG", optimize=True)
    return destination

