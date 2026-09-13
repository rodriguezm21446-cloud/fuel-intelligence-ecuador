from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from .ai import Editorial
from .calculations import direction
from .models import Candidate, FuelPrice, PriceChange
from .text import collapse_spaces, trim_x


ECUADOR_TIME_LABEL = "hora de Ecuador (UTC−5)"


def format_ecuador_time(value: datetime) -> str:
    """Render a user-facing timestamp with an unambiguous Ecuador label."""
    return f"{value:%d/%m/%Y %H:%M:%S} — {ECUADOR_TIME_LABEL}"


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
    sent_local: datetime | None = None,
) -> str:
    period = prices[0].period if prices else None
    upcoming = bool(period and period.start > detected_local.date())
    terminal = any("terminal automotriz" in item.product.lower() for item in prices)
    lines = [
        "CONFIRMADO — PRÓXIMO PERIODO DE COMBUSTIBLES | ECUADOR"
        if upcoming else
        "CONFIRMADO — ACTUALIZACIÓN DE COMBUSTIBLES | ECUADOR"
    ]
    if period:
        lines.append(f"Vigencia: {period.start:%d/%m/%Y} al {period.end:%d/%m/%Y}")
    lines.append(f"Detectado: {format_ecuador_time(detected_local)}")
    if sent_local is not None:
        lines.append(f"Enviado: {format_ecuador_time(sent_local)}")
    if upcoming:
        price_heading = "PRECIOS OFICIALES PRÓXIMOS EN TERMINAL:" if terminal else "PRECIOS OFICIALES PRÓXIMOS AL CONSUMIDOR:"
    else:
        price_heading = "PRECIOS VIGENTES EN TERMINAL:" if terminal else "PRECIOS VIGENTES AL CONSUMIDOR:"
    lines.extend(["", price_heading])
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


def format_news_alert(
    candidate: Candidate,
    detected_local: datetime,
    corroborated: bool,
    editorial: Editorial | None = None,
    sent_local: datetime | None = None,
) -> str:
    excerpt = collapse_spaces(candidate.summary or candidate.body)
    excerpt = excerpt[:700].rstrip()
    confirmation = "fuente primaria" if candidate.source.trust >= 90 else "coincidencia entre fuentes independientes"
    fallback_x = trim_x(f"{candidate.title}. {excerpt[:150]} Fuente: {candidate.source.name}", 280)
    x_text = editorial.x_text if editorial else fallback_x
    time_lines = [f"Detectado: {format_ecuador_time(detected_local)}"]
    if sent_local is not None:
        time_lines.append(f"Enviado: {format_ecuador_time(sent_local)}")
    impact = market_impact_channel(candidate)
    analysis = editorial.analysis if editorial else (
        "La fuente confirma el hecho descrito, pero no aporta evidencia suficiente para atribuirle por sí sola "
        "todo el movimiento del precio."
    )
    return "\n".join(
        [
            "SQUAWK CONFIRMADO — MERCADO DE COMBUSTIBLES",
            *time_lines,
            f"Verificación: {confirmation}",
            "",
            "HECHO:",
            candidate.title,
            "",
            "DATOS PUBLICADOS:",
            excerpt or "La fuente no proporcionó un resumen; revisa el enlace original.",
            "",
            "QUÉ ESTÁ PASANDO:",
            analysis,
            "",
            "CANAL HACIA ECUADOR:",
            impact,
            *((f"Dudas declaradas: {editorial.doubts}",) if editorial and editorial.doubts else ()),
            "",
            "TEXTO PARA X:",
            x_text,
            "",
            f"Fuente: {candidate.source.name}",
            candidate.url,
        ]
    )


def market_impact_channel(candidate: Candidate) -> str:
    """Explain the transmission channel without claiming a price forecast."""
    text = collapse_spaces(f"{candidate.title} {candidate.summary} {candidate.body}").lower()
    if any(term in text for term in ("refiner", "gasoline", "rbob", "diesel", "ulsd", "destil")):
        return (
            "Puede afectar referencias de gasolina o diésel importado y costos de abastecimiento. "
            "No demuestra por sí solo un cambio del precio regulado en Ecuador."
        )
    if any(term in text for term in ("inventar", "stock", "existenc", "reserv")):
        return (
            "Un cambio de inventarios puede presionar las cotizaciones internacionales; el efecto local depende "
            "del periodo de cálculo, importaciones, flete y fórmula oficial."
        )
    if any(term in text for term in ("brent", "wti", "crude", "crudo", "opec", "opep", "pipeline", "oleoduct")):
        return (
            "Es una señal de costo aguas arriba. Para trasladarla a Ecuador todavía deben comprobarse derivados, "
            "flete, ventana temporal y decisión oficial."
        )
    return (
        "Se mantiene como contexto de mercado. No hay evidencia suficiente para calcular un cambio del precio "
        "ecuatoriano a partir de esta noticia solamente."
    )


def format_unconfirmed_news_squawk(
    candidate: Candidate,
    detected_local: datetime,
    sent_local: datetime | None = None,
    editorial: Editorial | None = None,
) -> str:
    """Fast, clearly labelled single-source alert for a potentially material event."""
    excerpt = (editorial.analysis if editorial else collapse_spaces(candidate.summary or candidate.body))[:700].rstrip()
    headline = editorial.x_text if editorial else candidate.title
    lines = [
        "SQUAWK TEMPRANO — UNA SOLA FUENTE",
        f"Detectado: {format_ecuador_time(detected_local)}",
    ]
    if sent_local is not None:
        lines.append(f"Enviado: {format_ecuador_time(sent_local)}")
    lines.extend(
        [
            "",
            "HECHO REPORTADO:",
            headline,
            "",
            "DATOS DISPONIBLES:",
            excerpt or "La fuente no publicó un resumen verificable; revisa el enlace.",
            "",
            "ESTADO DE LA CAUSA:",
            "Aún no corroborada de forma independiente. No tratar correlación como causalidad.",
            "",
            "CANAL HACIA ECUADOR:",
            market_impact_channel(candidate),
            "",
            "Uso editorial: contexto y vigilancia; no publicar como hecho confirmado ni como predicción.",
            f"Fuente única: {candidate.source.name}",
            candidate.url,
        ]
    )
    return "\n".join(lines)


def format_unconfirmed_price_alert(
    candidate: Candidate,
    prices: list[FuelPrice],
    detected_local: datetime,
    reasons: tuple[str, ...],
    sent_local: datetime | None = None,
) -> str:
    """Early warning for a relevant report that is not safe to call official yet."""
    lines = [
        "ALERTA TEMPRANA — PRECIO TODAVÍA NO CONFIRMADO",
        f"Detectado: {format_ecuador_time(detected_local)}",
    ]
    if sent_local is not None:
        lines.append(f"Enviado: {format_ecuador_time(sent_local)}")
    lines.extend(["", candidate.title, "", "Valores mencionados por la fuente:"])
    period = next((item.period for item in prices if item.period), None)
    if period:
        lines.append(f"Vigencia reportada: {period.start:%d/%m/%Y}–{period.end:%d/%m/%Y}")
    if any(item.source_conflict for item in prices):
        lines.extend(["Hay cifras contradictorias. Revisar las fuentes antes de publicar.", candidate.url])
        return "\n".join(lines)
    lines.extend(f"{item.product}: ${_price(item.price)} por galón" for item in prices)
    lines.extend(
        [
            "",
            "Estado: no publicar todavía como precio oficial.",
            "Motivo: " + "; ".join(reasons[-3:]),
            "Puedes publicarlo únicamente como reporte atribuido a la fuente, dejando claro que no es una ficha oficial.",
            "Seguiremos buscando corroboración y correcciones del reporte. La ausencia de PDF no impide esta alerta.",
            "",
            "TEXTO PARA X:",
            trim_x(
                "Ecuador | Reporte de precios: "
                + " | ".join(f"{item.product}: ${_price(item.price)}" for item in prices)
                + f". Fuente: {candidate.source.name}. Pendiente de ficha oficial.",
                280,
            ),
            "",
            f"Fuente del reporte: {candidate.source.name}",
            candidate.url,
        ]
    )
    return "\n".join(lines)
