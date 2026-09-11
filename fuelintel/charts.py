from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from .chart_renderer import render_chart
from .storage import Store


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


def build_national_payload(store: Store, range_limit: int | None = 4) -> dict | None:
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
                "color": _color(label),
                "values": [float(by_date[day]["price"]) if day in by_date else None for day in dates],
                "pending": pending,
            }
        )

    # A decree is an event on the shared time axis, not an attribute to repeat
    # beside every series. Only annotate a transition observed inside the range.
    decrees_by_date: dict[str, tuple[str, ...]] = {}
    for day in dates:
        decrees_by_date[day] = tuple(
            sorted(
                {
                    row["decree_resolution"][:72]
                    for row in rows
                    if _when(row).date().isoformat() == day and row["decree_resolution"]
                }
            )
        )
    annotations = []
    previous_decrees = decrees_by_date[dates[0]]
    for index, day in enumerate(dates[1:], start=1):
        current_decrees = decrees_by_date[day]
        if current_decrees and current_decrees != previous_decrees:
            annotations.append({"index": index, "text": " / ".join(current_decrees)[:96]})
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

    newest = max(confirmed_rows, key=lambda row: row["verified_at"] or row["observed_at"])
    verified = datetime.fromisoformat((newest["verified_at"] or newest["observed_at"]).replace("Z", "+00:00"))
    verified = verified.astimezone(ZoneInfo("America/Guayaquil"))
    sources = "/".join(sorted({row["source_name"] for row in confirmed_rows if row["source_name"]})) or "fuente oficial"
    latest_values = [ds["values"][-1] for ds in datasets if ds["values"][-1] is not None]
    insight = "Último periodo confirmado; las líneas punteadas identifican datos pendientes."
    if latest_values:
        prefix = "Precio vigente más alto" if len(dates) == 1 else "Último precio más alto del historial"
        insight = f"{prefix}: ${max(latest_values):.3f}/galón. Consulte cada producto antes de publicar."
    type_label = {"PUBLICO": "precio al público", "TERMINAL": "precio en terminal", "REFERENCIAL": "precio referencial"}[selected_type]
    period_count = len(dates)
    if period_count == 1:
        title = "Precio vigente de combustible en Ecuador"
        period_description = f"Vigente desde {datetime.fromisoformat(dates[0]):%d/%m/%Y}"
    else:
        title = "Histórico de precios de combustible en Ecuador"
        period_description = (
            f"{period_count} periodos oficiales · "
            f"{datetime.fromisoformat(dates[0]):%d/%m/%Y}–{datetime.fromisoformat(dates[-1]):%d/%m/%Y}"
        )
    return {
        "title": title,
        "subtitle": f"{period_description} · {type_label} · USD por galón",
        "labels": [datetime.fromisoformat(day).strftime("%d/%m/%Y") for day in dates],
        "datasets": datasets,
        "annotations": annotations,
        "references": references,
        "stepped": True,
        "insight": insight,
        "source": f"Fuente: {sources} · Verificado: {verified:%d/%m/%Y %H:%M} ECT",
        "credit": own_data_label(),
    }


def generate_dashboard(store: Store, destination: Path, now_local: datetime, demo: bool = False) -> Path | None:
    payload = build_national_payload(store)
    if payload is None:
        return None
    if demo:
        payload["subtitle"] += " · Vista previa con datos de prueba"
    return render_chart(payload, destination)


def generate_historical_dashboard(store: Store, destination: Path) -> Path | None:
    payload = build_national_payload(store, range_limit=None)
    if payload is None:
        return None
    payload["title"] = "Histórico completo de precios de combustible en Ecuador"
    return render_chart(payload, destination)

