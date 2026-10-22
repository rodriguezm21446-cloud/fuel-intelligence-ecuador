from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .ai import Editorial
from .calculations import direction
from .models import Candidate, FuelPrice, PriceChange
from .text import collapse_spaces, trim_x


ECUADOR_TIME_LABEL = "hora de Ecuador (UTC−5)"


@dataclass(frozen=True, slots=True)
class TelegramSections:
    """A delivery plan whose entries must be sent as separate messages."""

    squawk: str
    context: str
    forecast: str = ""

    def messages(self) -> tuple[str, ...]:
        return tuple(part for part in (self.squawk, self.context, self.forecast) if part.strip())


def format_ecuador_time(value: datetime) -> str:
    """Render a user-facing timestamp with an unambiguous Ecuador label."""
    return f"{value:%d/%m/%Y %H:%M:%S} — {ECUADOR_TIME_LABEL}"


def _price(value: Decimal) -> str:
    rendered = f"{value:.3f}"
    return rendered.rstrip("0").rstrip(".")


def _price_movement(product: str, current: Decimal, change: PriceChange | None, *, compact: bool = False) -> str:
    current_text = _price(current)
    if change is None or change.previous is None:
        return f"{product} ${current_text} (sin base previa)"
    previous_text = _price(change.previous)
    if change.absolute == 0:
        return f"{product} ${current_text} (antes ${previous_text}; sin cambio)"
    sign = "+" if change.absolute and change.absolute > 0 else ""
    if compact:
        return f"{product} ${current_text} (antes ${previous_text})"
    percent = "" if change.percent is None else f"; {sign}{change.percent}%"
    return f"{product} ${current_text} (antes ${previous_text}; {sign}${change.absolute}{percent})"


def _price_squawk(prefix: str, prices: list[FuelPrice], changes: dict[str, PriceChange], source: str) -> str:
    """Keep every confirmed current/previous price before shortening prose."""
    detailed = "; ".join(_price_movement(item.product, item.price, changes.get(item.product)) for item in prices)
    message = f"{prefix}: {detailed}. Fuente: {source}."
    if len(message) <= 300:
        return message
    compact = "; ".join(
        _price_movement(item.product, item.price, changes.get(item.product), compact=True) for item in prices
    )
    message = f"{prefix}: {compact}. Fuente oficial."
    if len(message) <= 300:
        return message
    # Current values are the irreducible facts. Never silently cut a number.
    # The known Ecuador product set fits; an unexpected oversized set fails
    # closed so an operator sees the schema problem instead of a partial post.
    current_only = "; ".join(f"{item.product} ${_price(item.price)}" for item in prices)
    message = f"{prefix}: {current_only}. Fuente oficial."
    if len(message) > 300:
        raise ValueError("el boletín excede 300 caracteres; se bloqueó para no cortar cifras")
    return message


def compose_x_prices(
    prices: list[FuelPrice], changes: dict[str, PriceChange], source: str = "ARCH/Petroecuador"
) -> str:
    period = prices[0].period if prices else None
    heading = "Precios de combustibles"
    if period:
        heading = f"Precios de combustibles | {period.start:%d/%m}–{period.end:%d/%m}"
    return _price_squawk(heading, prices, changes, source)


def build_price_sections(
    candidate: Candidate,
    prices: list[FuelPrice],
    changes: dict[str, PriceChange],
    detected_local: datetime,
    editorial: Editorial | None = None,
    sent_local: datetime | None = None,
    forecast: str = "",
) -> TelegramSections:
    """Create a short publishable price bulletin and a separate audit note."""
    period = prices[0].period if prices else None
    period_text = f"{period.start:%d/%m}–{period.end:%d/%m}" if period else "periodo por verificar"
    squawk = _price_squawk(
        f"Ecuador | Combustibles {period_text}", prices, changes, candidate.source.name
    )
    analysis = editorial.analysis if editorial else (
        "Los valores, la unidad y la vigencia fueron comprobados con aritmética decimal. "
        "No se atribuye una causa si el documento o una fuente verificable no la explica."
    )
    status = "fuente primaria" if candidate.source.trust >= 90 else "fuente corroborada"
    context = "\n".join(
        [
            f"CONTEXTO · confirmado mediante {status}",
            f"Detectado: {format_ecuador_time(detected_local)}",
            *((f"Enviado: {format_ecuador_time(sent_local)}",) if sent_local else ()),
            "Detalle numérico comprobado:",
            *(_price_movement(item.product, item.price, changes.get(item.product)) for item in prices),
            f"Lectura: {analysis}",
            *((f"Dudas: {editorial.doubts}",) if editorial and editorial.doubts else ()),
            f"Fuente: {candidate.source.name}",
            candidate.url,
        ]
    )
    return TelegramSections(squawk=squawk, context=context, forecast=forecast)


NEWS_TEXT_LIMIT = 400


def _fit_news_context(header: list[str], optional: list[str], tail: list[str]) -> str:
    """Keep the header and the source/url tail; add optional blocks while they fit."""
    kept: list[str] = []
    for block in optional:
        if len("\n".join(header + kept + [block] + tail)) <= NEWS_TEXT_LIMIT:
            kept.append(block)
        else:
            break
    return "\n".join(header + kept + tail)


def build_news_sections(
    candidate: Candidate,
    detected_local: datetime,
    corroborated: bool,
    editorial: Editorial | None = None,
    sent_local: datetime | None = None,
    *,
    confirmed: bool = True,
) -> TelegramSections:
    """Create a concise summary (≤400 chars) plus a source/context note, never a monolith."""
    excerpt = collapse_spaces(candidate.summary or candidate.body)
    fallback = trim_x(
        f"{candidate.title}. {excerpt[:160]} Fuente: {candidate.source.name}.",
        NEWS_TEXT_LIMIT,
    )
    summary = editorial.summary if editorial and editorial.summary else ""
    squawk = trim_x(summary or fallback, NEWS_TEXT_LIMIT)
    if not confirmed:
        status = "una sola fuente; pendiente de corroboración"
    elif candidate.source.trust >= 90:
        status = "fuente primaria"
    elif corroborated:
        status = "dos fuentes independientes"
    else:
        status = "fuente atribuida"
    default_analysis = "Correlación de mercado aún sin evidencia."
    analysis = editorial.analysis if editorial else default_analysis
    header = [
        f"CONTEXTO · {status}",
        f"Detectado: {format_ecuador_time(detected_local)}",
    ]
    if sent_local:
        header.append(f"Enviado: {format_ecuador_time(sent_local)}")
    optional = [f"Qué sabemos: {analysis}", f"Canal hacia Ecuador: {market_impact_channel(candidate)}"]
    if editorial and editorial.doubts:
        optional.append(f"Dudas: {editorial.doubts}")
    tail = [f"Fuente: {candidate.source.name}", candidate.url]
    return TelegramSections(squawk=squawk, context=_fit_news_context(header, optional, tail))


def _has_real_change(changes: dict[str, PriceChange] | None) -> bool:
    return bool(changes) and any(
        change is not None and change.previous is not None and change.absolute not in (None, 0)
        for change in changes.values()
    )


def _change_lines(prices: list[FuelPrice], changes: dict[str, PriceChange] | None) -> list[str]:
    if not _has_real_change(changes):
        return []
    return [
        _price_movement(item.product, item.price, (changes or {}).get(item.product))
        for item in prices
    ]


def build_unconfirmed_price_sections(
    candidate: Candidate,
    prices: list[FuelPrice],
    detected_local: datetime,
    reasons: tuple[str, ...],
    sent_local: datetime | None = None,
    changes: dict[str, PriceChange] | None = None,
) -> TelegramSections:
    period = next((item.period for item in prices if item.period), None)
    period_text = f"{period.start:%d/%m}–{period.end:%d/%m}" if period else "vigencia pendiente"
    changed = _has_real_change(changes)
    if changed:
        values = " | ".join(
            _price_movement(item.product, item.price, (changes or {}).get(item.product), compact=True)
            for item in prices
        )
    else:
        values = " | ".join(f"{item.product}: ${_price(item.price)}" for item in prices)
    marker = " · CAMBIO DETECTADO" if changed else ""
    squawk = trim_x(
        f"Ecuador | Reporte de combustibles {period_text}{marker}: {values}. "
        f"Aún no es precio oficial. Fuente: {candidate.source.name}.",
        300,
    )
    context = "\n".join(
        [
            "PENDIENTE — No publicar todavía como precio oficial.",
            f"Detectado: {format_ecuador_time(detected_local)}",
            *((f"Enviado: {format_ecuador_time(sent_local)}",) if sent_local else ()),
            "Motivo: " + "; ".join(reasons[-3:]),
            *(
                ["Variación frente al último precio confirmado:", *_change_lines(prices, changes)]
                if changed
                else []
            ),
            f"Fuente del reporte: {candidate.source.name}",
            candidate.url,
        ]
    )
    return TelegramSections(squawk=squawk, context=context)


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
    excerpt = (editorial.analysis if editorial else collapse_spaces(candidate.summary or candidate.body))[:1100].rstrip()
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
