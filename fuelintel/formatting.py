from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from .ai import Editorial
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
    editorial: Editorial | None = None,
) -> str:
    period = prices[0].period if prices else None
    terminal = any("terminal automotriz" in item.product.lower() for item in prices)
    lines = ["CONFIRMADO — ACTUALIZACIÓN DE COMBUSTIBLES | ECUADOR"]
    if period:
        lines.append(f"Vigencia: {period.start:%d/%m/%Y} al {period.end:%d/%m/%Y}")
    lines.append(f"Detectado: {detected_local:%d/%m/%Y %H:%M:%S}")
    lines.extend(["", "PRECIOS VIGENTES EN TERMINAL:" if terminal else "PRECIOS VIGENTES AL CONSUMIDOR:"])
    for item in prices:
        lines.append(f"{item.product}: ${_price(item.price)} por galón")
    lines.extend(["", "VARIACIÓN FRENTE AL PERIODO ANTERIOR:"])
    for item in prices:
        change = changes[item.product]
        if change.absolute is None:
            delta = "sin historial comparable"
        else:
            sign = "+" if change.absolute > 0 else ""
            delta = f"{sign}${change.absolute} ({sign}{change.percent}%) — {direction(change)}"
        lines.append(f"{item.product}: {delta}")
    analysis = editorial.analysis if editorial else (
        "Python verificó rangos, unidades, vigencia y variaciones con aritmética decimal. "
        "Los factores causales solo se añadirán cuando estén respaldados por fuentes."
    )
    x_text = editorial.x_text if editorial else compose_x_prices(prices, changes)
    lines.extend(
        [
            "",
            "",
            "POR QUÉ CAMBIÓ:",
            analysis,
            *((f"Dudas declaradas: {editorial.doubts}",) if editorial and editorial.doubts else ()),
            "",
            "TEXTO PARA X:",
            x_text,
            "",
            f"Fuente: {candidate.source.name}",
            candidate.url,
        ]
    )
    return "\n".join(lines)


def format_news_alert(candidate: Candidate, detected_local: datetime, corroborated: bool, editorial: Editorial | None = None) -> str:
    excerpt = collapse_spaces(candidate.summary or candidate.body)
    excerpt = excerpt[:700].rstrip()
    confirmation = "fuente primaria" if candidate.source.trust >= 90 else "coincidencia entre fuentes independientes"
    fallback_x = trim_x(f"{candidate.title}. {excerpt[:150]} Fuente: {candidate.source.name}", 280)
    x_text = editorial.x_text if editorial else fallback_x
    return "\n".join(
        [
            "CONFIRMADO — NOTICIA DE COMBUSTIBLES",
            f"Detectado: {detected_local:%d/%m/%Y %H:%M:%S}",
            f"Verificación: {confirmation}",
            "",
            candidate.title,
            excerpt or "La fuente no proporcionó un resumen; revisa el enlace original.",
            *(("", f"ANÁLISIS: {editorial.analysis}") if editorial else ()),
            *((f"Dudas declaradas: {editorial.doubts}",) if editorial and editorial.doubts else ()),
            "",
            "TEXTO PARA X:",
            x_text,
            "",
            f"Fuente: {candidate.source.name}",
            candidate.url,
        ]
    )

