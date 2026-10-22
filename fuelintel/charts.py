from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

from .chart_renderer import render_chart
from .storage import Store
from .models import FuelPrice
from .text import fold


WIDTH, HEIGHT = 3200, 1800
PRODUCT_COLORS = {
    "Diésel": "#4A90D9",
    "Extra/Ecopaís": "#8A8A8A",
    "Extra": "#8A8A8A",
    "Ecopaís": "#8A8A8A",
    "Súper": "#E8E8E8",
    "Premium": "#6FA85C",
}


def choose_chart_kind(dataset: str, points: int, has_ohlc: bool = False) -> str:
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
    raw = os.getenv("FUELINTEL_BRAND_NAME", "@combustibleEc")
    clean = " ".join(raw.split())[:48]
    return clean or "@combustibleEc"


def own_data_label(estimated: bool = False) -> str:
    if estimated:
        return f"Estimación propia · {brand_name()} · No es dato oficial"
    return f"Cálculos y visualización: {brand_name()}"


def _when(row) -> datetime:
    raw = row["effective_from"] or row["observed_at"]
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _product_label(product: str) -> str:
    if product.startswith("Diésel"):
        return "Diésel"
    if product.startswith("Súper"):
        return "Súper"
    if product.startswith("Premium"):
        return "Premium"
    return product.split(" (")[0]


def _color(label: str) -> str:
    return PRODUCT_COLORS.get(label, "#8A8A8A")


EDITORIAL_COLORS = {
    "Extra/Ecopaís": "#1F6E8C",
    "Extra": "#1F6E8C",
    "Ecopaís": "#1F6E8C",
    "Súper": "#C07038",
    "Diésel": "#17375E",
}

MONTH_LENGTH = 30.44

CONTEXT_TERMS = (
    "gasolina", "dies", "combustible", "petroecuador", "ecopais",
    "extra", "super", "subsidio", "aranceles", "petrol", "crudo", "refiner",
)
STRONG_CONTEXT_TERMS = (
    "gasolina", "dies", "combustible", "petroecuador", "ecopais",
    "extra", "super", "subsidio", "aranceles",
)


def _editorial_color(label: str) -> str:
    return EDITORIAL_COLORS.get(label, "#8A8A8A")


def _fold(value) -> str:
    return fold(value)


def _news_context_bullet(store: Store, now_utc: datetime) -> str:
    """One sourced headline ties the chart to what the operator already read.

    Gasoline/subsidy stories outrank generic oil stories because the national
    chart is about pump and terminal prices.
    """
    items = store.recent_notified_market_news(now=now_utc, hours=72, limit=6)
    fallback = ""
    for item in items:
        title = " ".join(str(item.get("title", "")).split())
        folded = fold(title + " " + str(item.get("summary", "")))
        if not any(term in folded for term in CONTEXT_TERMS):
            continue
        source = str(item.get("source_name", "")).strip()
        when = str(item.get("published_at") or item.get("detected_at") or "")[:10]
        clip = title[:110] + ("…" if len(title) > 110 else "")
        if source and when:
            fallback = f"Noticia relevante: {clip} ({source}, {when})"
        else:
            fallback = f"Noticia relevante: {clip}"
        if any(term in folded for term in STRONG_CONTEXT_TERMS):
            return fallback
    return fallback


def _decree_annotation(rows: list) -> str:
    """Shortest possible official identifier of the last price adjustment."""
    for row in sorted(rows, key=_when, reverse=True):
        raw = str(row["decree_resolution"] or "")
        if not raw.strip():
            continue
        head = re.split(r"\s+nota\b", raw, flags=re.IGNORECASE)[0]
        short = re.split(r"\s*:", head, maxsplit=1)[0][:64].strip(" ,")
        if short:
            return short
    return ""


def _money(value: float) -> str:
    return f"${value:.2f}".replace(".", ",")


def _movement_title(dates: list[str], datasets: list[dict]) -> str | None:
    """Declarative headline: the series with the largest move states the finding."""
    best = None
    for series in datasets:
        values = [value for value in series["values"] if value is not None]
        if len(values) < 2:
            continue
        first, last = values[0], values[-1]
        change = (last - first) / first * 100
        if best is None or abs(change) > abs(best[2]):
            best = (first, last, change, series["label"])
    if best is None:
        return None
    first, last, change, best_label = best
    try:
        start = datetime.fromisoformat(dates[0])
        end = datetime.fromisoformat(dates[-1])
    except ValueError:
        return None
    months = max(1, round((end - start).days / MONTH_LENGTH))
    if abs(change) < 0.5:
        return f"{best_label} se mantiene en {_money(last)} en {months} meses"
    direction = "sube" if change > 0 else "cae"
    return f"{best_label} {direction} {abs(change):.0f}% en {months} meses: de {_money(first)} a {_money(last)}"


def _minimum_markers(datasets: list[dict], labels_count: int) -> list[dict]:
    """Flag the official minimum of each series as an GS-style callout,
    unless the minimum is the first or last point (endpoints already show it)."""
    markers = []
    for dataset_index, series in enumerate(datasets):
        positions = [index for index, value in enumerate(series["values"]) if value is not None]
        if len(positions) < 4:
            continue
        first, last = positions[0], positions[-1]
        value_index = min(positions, key=lambda index: series["values"][index])
        if value_index in (first, last):
            continue
        if not 0 < value_index < labels_count:
            continue
        markers.append(
            {
                "datasetIndex": dataset_index,
                "index": value_index,
                "text": f"Mín. {_money(series['values'][value_index])}",
            }
        )
    return markers


def _combine_extra_ecopais(grouped: dict[str, list]) -> dict[str, list]:
    extra = grouped.get("Extra")
    ecopais = grouped.get("Ecopaís")
    if not extra or not ecopais:
        return grouped
    extra_values = {(_when(row).date().isoformat(), row["price"]) for row in extra}
    eco_values = {(_when(row).date().isoformat(), row["price"]) for row in ecopais}
    if extra_values == eco_values:
        grouped = dict(grouped)
        grouped["Extra/Ecopaís"] = extra
        grouped.pop("Extra", None)
        grouped.pop("Ecopaís", None)
    return grouped


def build_national_payload(store: Store, range_limit: int | None = 12) -> dict | None:
    rows = store.recent_prices(limit_per_product=10000 if range_limit is None else 24)
    if not rows:
        return None
    available_types = {row["price_type"] for row in rows}
    selected_type = next((kind for kind in ("PUBLICO", "TERMINAL", "REFERENCIAL") if kind in available_types), None)
    if selected_type is None:
        return None
    rows = [row for row in rows if row["price_type"] == selected_type]
    confirmed_rows = [row for row in rows if row["verification_status"] == "CONFIRMED"]
    dates = sorted({_when(row).date().isoformat() for row in confirmed_rows})
    if range_limit is not None:
        dates = dates[-range_limit:]
    if not dates:
        return None

    grouped: dict[str, list] = defaultdict(list)
    for row in rows:
        if _when(row).date().isoformat() in dates:
            grouped[_product_label(row["product"])].append(row)
    grouped = _combine_extra_ecopais(grouped)

    datasets = []
    for label, product_rows in sorted(grouped.items()):
        by_date = {_when(row).date().isoformat(): row for row in product_rows}
        pending = any(row["verification_status"] != "CONFIRMED" for row in product_rows)
        datasets.append(
            {
                "label": label,
                "color": _editorial_color(label),
                "values": [float(by_date[day]["price"]) if day in by_date else None for day in dates],
                "pending": pending,
            }
        )

    # A decree is an event on the shared time axis, not an attribute to repeat
    # beside every series. Only annotate a transition observed inside the range.
    # GS-style: the line marks the change; the text stays short ("Decreto 123").
    decrees_by_date: dict[str, tuple[str, ...]] = {}
    for day in dates:
        labels_for_day: set[str] = set()
        for row in rows:
            if _when(row).date().isoformat() == day and row["decree_resolution"]:
                head = re.split(r"\s+nota\b", row["decree_resolution"], flags=re.IGNORECASE)[0]
                short = re.split(r"\s*:", head, maxsplit=1)[0][:34].strip()
                if short:
                    labels_for_day.add(short)
        decrees_by_date[day] = tuple(sorted(labels_for_day))
    annotations = []
    previous_decrees = decrees_by_date[dates[0]]
    for index, day in enumerate(dates[1:], start=1):
        current_decrees = decrees_by_date[day]
        if current_decrees and current_decrees != previous_decrees:
            first_current = current_decrees[0].strip()
            if not any(first_current in decree for decree in previous_decrees):
                annotations.append({"index": index, "text": first_current[:30]})
        previous_decrees = current_decrees

    metrics = {(row["product"], row["price_type"]): row for row in store.price_metrics()}
    references = []
    for label, product_rows in sorted(grouped.items()):
        original = product_rows[-1]["product"]
        metric = metrics.get((original, selected_type))
        if metric:
            references.append(
                {
                    "label": label,
                    "color": _color(label),
                    "min": float(metric["historical_min"]),
                    "max": float(metric["historical_max"]),
                }
            )
    if len(references) != len(grouped):
        references = []

    displayed_confirmed = [row for row in confirmed_rows if _when(row).date().isoformat() in dates]
    source_names = sorted({row["source_name"] for row in displayed_confirmed if row["source_name"]})
    sources = "/".join(source_names) or "fuente oficial"
    source_urls = [str(row["source_url"] or "").casefold() for row in displayed_confirmed]
    official_document = bool(source_urls) and all(
        url.endswith(".pdf")
        or ".pdf?" in url
        or "download-monitor/download.php" in url
        for url in source_urls
    )
    source_medium = "PDF oficial" if official_document else "Publicación oficial"
    type_label = {"PUBLICO": "precio al público", "TERMINAL": "precio en terminal", "REFERENCIAL": "precio referencial"}[selected_type]
    period_start = min(_when(row).date() for row in displayed_confirmed)
    period_end = max(
        datetime.fromisoformat(str(row["effective_to"] or _when(row).date().isoformat())[:10]).date()
        for row in displayed_confirmed
    )
    exact_period = f"{period_start:%d/%m/%Y}–{period_end:%d/%m/%Y}"
    period_count = len(dates)
    period_description = f"Vigencia {exact_period}" if period_count == 1 else f"{period_count} periodos oficiales · {exact_period}"
    title = _movement_title(
        [datetime.fromisoformat(day).date().isoformat() for day in dates], datasets
    )
    if not title:
        title = "Precio vigente de combustible en Ecuador" if period_count == 1 else "Histórico de precios de combustible en Ecuador"
    minimums = _minimum_markers(datasets, len(dates))
    # Context first, chart second: the reading arrives with the evidence.
    # Decree line is deterministic (from official metadata); news line cites a
    # headline the operator already approved, never an invented explanation.
    context_bullets: list[str] = []
    decree = _decree_annotation(displayed_confirmed)
    if decree:
        context_bullets.append(f"Ajuste oficial: {decree}")
    news_bullet = ""
    try:
        news_bullet = _news_context_bullet(store, datetime.now(timezone.utc))
    except Exception:
        news_bullet = ""
    if news_bullet:
        context_bullets.append(news_bullet)
    return {
        "title": title,
        "subtitle": f"{period_description} · {type_label} · USD por galón",
        "priceType": selected_type,
        "context": context_bullets[:2],
        "labels": [datetime.fromisoformat(day).strftime("%d/%m/%Y") for day in dates],
        "datasets": datasets,
        "annotations": annotations,
        "references": references,
        "minimums": minimums,
        "chartKind": "step",
        "stepped": True,
        "insight": "",
        "source": f"Fuente: {source_medium} de {sources} · Periodo: {exact_period} · {own_data_label()}",
        "credit": own_data_label(),
        "displayDecimals": 2,
    }


def generate_dashboard(store: Store, destination: Path, now_local: datetime, demo: bool = False) -> Path | None:
    payload = build_national_payload(store)
    if payload is None:
        return None
    if demo:
        payload["subtitle"] += " · Vista previa con datos de prueba"
    return render_chart(payload, destination)


def national_data_signature(store: Store) -> str | None:
    """Hash the verified chart evidence, not rendered PNG bytes.

    Rendered images may differ because of browser metadata or timestamps even
    when the underlying prices are unchanged.  This signature therefore makes
    scheduled delivery idempotent at the data level.
    """
    payload = build_national_payload(store)
    if payload is None:
        return None
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def generate_historical_dashboard(store: Store, destination: Path) -> Path | None:
    payload = build_national_payload(store, range_limit=None)
    if payload is None:
        return None
    payload["subtitle"] = f"Histórico completo · {payload['subtitle']}"
    return render_chart(payload, destination)


def generate_reported_price_chart(
    store: Store,
    prices: list[FuelPrice],
    source_name: str,
    destination: Path,
    now_local: datetime,
) -> Path | None:
    """Render a clearly provisional media report without changing official history."""
    allowed = {"Extra", "Ecopaís", "Diésel Premium", "Súper"}
    by_product = {item.product: item for item in prices if item.product in allowed}
    if not by_product:
        return None
    if by_product.get("Extra") and by_product.get("Ecopaís") and by_product["Extra"].price == by_product["Ecopaís"].price:
        by_product["Extra/Ecopaís"] = by_product.pop("Extra")
        by_product.pop("Ecopaís")
    datasets = []
    for product, item in sorted(by_product.items()):
        previous_product = "Extra" if product == "Extra/Ecopaís" else product
        previous = store.previous_price(previous_product, item.period.start.isoformat() if item.period else None)
        label = "Diésel" if product.startswith("Diésel") else product
        datasets.append({
            "label": label,
            "color": _color(label),
            "values": [float(previous) if previous is not None else None, float(item.price)],
            "pending": True,
        })
    period = next((item.period for item in by_product.values() if item.period), None)
    validity = f" · posible vigencia {period.start:%d/%m/%Y}–{period.end:%d/%m/%Y}" if period else ""
    payload = {
        "title": "Reporte preliminar de precios de combustible",
        "subtitle": f"Información publicada por un medio{validity} · precio al público en estación · USD por galón",
        "priceType": "PUBLICO",
        "labels": ["Anterior confirmado", "Reporte reciente"],
        "datasets": datasets,
        "annotations": [],
        "references": [],
        "chartKind": "bar",
        "stepped": False,
        "insight": "",
        "source": (
            f"Fuente: {source_name} · Periodo: {period.start:%d/%m/%Y}–{period.end:%d/%m/%Y}"
            if period else f"Fuente: {source_name} · Fecha: {now_local:%d/%m/%Y}"
        ),
        "credit": own_data_label(),
        "displayDecimals": 3,
    }
    return render_chart(payload, destination)
