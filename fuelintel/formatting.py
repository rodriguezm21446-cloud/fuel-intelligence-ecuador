from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from .calculations import direction
from .models import Candidate, FuelPrice, PriceChange
from .text import collapse_spaces, trim_x


def _price(value: Decimal) -> str:
    rendered = f"{value:.3f}"
    return rendered.rstrip("0").rstrip(".")


def compose_x_prices(prices: list[FuelPrice], changes: dict[str, PriceChange]) -> str:
    period = prices[0].period if prices else None
    heading = "PRECIOS DE COMBUSTIBLES"
    if period:
        heading = f"PRECIOS DE COMBUSTIBLES | {period.start:%d/%m}–{period.end:%d/%m}"
    values = " | ".join(f"{item.product}: ${_price(item.price)}" for item in prices)
    changed = [change for change in changes.values() if change.absolute not in {None, Decimal("0") }]
    note = "Sin cambios frente al periodo anterior." if not changed and changes else "Cambios frente al periodo anterior incluidos en el análisis."
    return trim_x(f"{heading}\n{values}\n{note}\nFuente oficial: ARCH/Petroecuador", 280)


def format_price_alert(
    candidate: Candidate,
    prices: list[FuelPrice],
    changes: dict[str, PriceChange],
    detected_local: datetime,
) -> str:
    period = prices[0].period if prices else None
    lines = ["CONFIRMADO — PRECIOS DE COMBUSTIBLES"]
    if period:
        lines.append(f"Vigencia: {period.start:%d/%m/%Y} al {period.end:%d/%m/%Y}")
    lines.append(f"Detectado: {detected_local:%d/%m/%Y %H:%M:%S}")
    lines.append("")
    for item in prices:
        change = changes[item.product]
        if change.absolute is None:
            delta = "sin historial comparable"
        else:
            sign = "+" if change.absolute > 0 else ""
            delta = f"{sign}${change.absolute} ({sign}{change.percent}%) — {direction(change)}"
        lines.append(f"{item.product}: ${_price(item.price)} por galón | {delta}")
    lines.extend(
        [
            "",
            "Análisis: Python verificó rangos, unidades, vigencia y variaciones con aritmética decimal. "
            "Los factores causales solo se añadirán cuando estén respaldados por fuentes.",
            "",
            "TEXTO PARA X:",
            compose_x_prices(prices, changes),
            "",
            f"Fuente: {candidate.source.name}",
            candidate.url,
        ]
    )
    return "\n".join(lines)


def format_news_alert(candidate: Candidate, detected_local: datetime, corroborated: bool) -> str:
    excerpt = collapse_spaces(candidate.summary or candidate.body)
    excerpt = excerpt[:700].rstrip()
    confirmation = "fuente primaria" if candidate.source.trust >= 90 else "coincidencia entre fuentes independientes"
    x_text = trim_x(f"{candidate.title}. {excerpt[:150]} Fuente: {candidate.source.name}", 280)
    return "\n".join(
        [
            "CONFIRMADO — NOTICIA DE COMBUSTIBLES",
            f"Detectado: {detected_local:%d/%m/%Y %H:%M:%S}",
            f"Verificación: {confirmation}",
            "",
            candidate.title,
            excerpt or "La fuente no proporcionó un resumen; revisa el enlace original.",
            "",
            "TEXTO PARA X:",
            x_text,
            "",
            f"Fuente: {candidate.source.name}",
            candidate.url,
        ]
    )

