from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import os
from functools import partial
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

from .anomalies import detect_anomalies
from .chart_renderer import render_chart, render_heatmap
from .charts import own_data_label
from .finance import percentile_rank, realized_volatility, return_correlation, volatility_series
from .formatting import TelegramSections
from .knowledge import within_band
from .market_math import percentage_change, validate_market_series
from .quality import quarantine
from .text import trim_x


ECUADOR_TZ = ZoneInfo("America/Guayaquil")
MATTE = {
    "WTI": "#C69236",
    "Brent": "#D8D5CE",
    "RBOB": "#A88452",
    "ULSD": "#6C8FB3",
    "Crack gasolina": "#A88452",
    "Crack diésel": "#6C8FB3",
}
GALLONS_PER_BARREL = 42
RETAIL_COLORS = {
    "Gasolina regular": "#9A9285",
    "Gasolina premium": "#D8D5CE",
    "Diésel": "#6C8FB3",
}
# Cotizaciones oficiales de spot (EIA). La ruta principal es la API legacy v1
# (requiere EIA_API_KEY); la segunda vía es el histórico oficial en eia.gov/dnav
# (hist_xls, archivos .xls de ~0.5 MB SIN key). Nada de agregadores privados:
# las series son las mismas que publica eia.gov y pasan por las bandas sane de
# knowledge.py antes de dibujarse.
EIA_SPOT_SERIES = {
    "WTI": "PET.RWTC.D",          # Cushing OK WTI Spot (USD/barril, diario)
    "Brent": "PET.RBRTE.D",       # Europe Brent Spot FOB (USD/barril, diario)
    "RBOB": "EER_EPMRU_PF4_Y35NY_DPG.D",      # NY Harbor gasolina regular spot (USD/gal)
    "ULSD": "EER_EPD2DXL0_PF4_Y35NY_DPG.D",   # NY Harbor diésel ULSD spot (USD/gal)
}

# Históricos oficiales keyless de eia.gov/dnav: un .xls por serie con la misma
# serie que la API (los ids de dnav son los mismos menos el prefijo PET.).
# La ruta RBOB antigua (EER_EPMR_PI_Y35NY_DPG) quedó descontinuada en 2019.
DNAV_XLS = {
    "WTI": "RWTCd.xls",
    "Brent": "RBRTEd.xls",
    "RBOB": "EER_EPMRU_PF4_Y35NY_DPGd.xls",
    "ULSD": "EER_EPD2DXL0_PF4_Y35NY_DPGd.xls",
}
DNAV_URL = "https://www.eia.gov/dnav/pet/hist_xls/"
_MAX_CACHE_ROWS = 420


@dataclass(frozen=True, slots=True)
class MarketEstimate:
    horizon_sessions: int
    sample_size: int
    probability_up: int
    probability_flat: int
    probability_down: int
    median_change: float
    publishable: bool
    reason: str


def _history(series_id: str) -> list[tuple[str, float]]:
    """Read official daily spot closes from the EIA API (legacy v1 route).

    Returns only observed dates with a finite value; the caller still runs the
    series through validate_market_series and the knowledge sanity bands, so a
    wrong series or a provider outage yields nothing rather than a guess.
    """
    import urllib.parse
    import urllib.request

    api_key = os.getenv("EIA_API_KEY", "").strip()
    if not api_key:
        return []
    # EIA v1 expects full ids ("PET.RWTC.D"); dnav-style short ids work in
    # modern EIA pages, so try both spellings before giving up.
    candidate_ids = [series_id]
    if not series_id.startswith("PET."):
        candidate_ids.append(f"PET.{series_id}")
    for effective_id in candidate_ids:
        url = "https://api.eia.gov/series/?" + urllib.parse.urlencode({
            "api_key": api_key,
            "series_id": effective_id,
        })
        request = urllib.request.Request(url, headers={"User-Agent": "FuelIntelligenceEcuador/0.4"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            continue
        rows: list[tuple[str, float]] = []
        for entry in payload.get("series", [{}])[0].get("data", []):
            if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                continue
            day, raw_value = str(entry[0]), entry[1]
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                continue
            if len(day) == 8 and math.isfinite(value) and value > 0:
                rows.append((f"{day[:4]}-{day[4:6]}-{day[6:8]}", value))
        if rows:
            return sorted(set(rows))
    return []


def _history_dnav(name: str, *, body: bytes | None = None) -> list[tuple[str, float]]:
    """Keyless official fallback: the .xls history that eia.gov/dnav publishes.

    Downloading the ~0.5 MB workbook from eia.gov needs no API key, so the
    market charts do not depend on a single secret. Only observed dates with a
    finite positive value are returned; downstream band validation still gates
    what gets drawn. `body` accepts pre-fetched workbook bytes (tests).
    """
    try:
        import xlrd
    except ImportError:
        raise RuntimeError("xlrd no está instalado: no se puede leer el histórico de eia.gov/dnav")
    if body is None:
        import urllib.request
        request = urllib.request.Request(
            DNAV_URL + DNAV_XLS[name], headers={"User-Agent": "FuelIntelligenceEcuador/0.4"}
        )
        with urllib.request.urlopen(request, timeout=25) as response:
            body = response.read()
    book = xlrd.open_workbook(file_contents=body)
    sheet = book.sheet_by_name("Data 1")
    epoch = datetime(1899, 12, 30)
    rows: list[tuple[str, float]] = []
    for index in range(3, sheet.nrows):
        serial, raw_value = sheet.cell_value(index, 0), sheet.cell_value(index, 1)
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if not (serial and math.isfinite(value) and value > 0):
            continue
        day = (epoch + timedelta(days=int(serial))).date().isoformat()
        rows.append((day, value))
    return sorted(set(rows))


def _weekly_closes(rows: list[tuple[str, float]]) -> list[tuple[str, float]]:
    weeks: dict[tuple[int, int], tuple[str, float]] = {}
    for day, value in sorted(rows):
        parsed = date.fromisoformat(day)
        iso = parsed.isocalendar()
        weeks[(iso.year, iso.week)] = (day, value)
    return list(weeks.values())


def _change(rows: list[tuple[str, float]], sessions: int) -> float | None:
    if len(rows) <= sessions or rows[-1][1] <= 0 or rows[-1 - sessions][1] <= 0:
        return None
    return percentage_change(rows[-1][1], rows[-1 - sessions][1])


def _rolling_volatility(rows: list[tuple[str, float]], window: int = 20) -> list[tuple[str, float]]:
    returns: list[tuple[str, float]] = []
    for (day, value), (_, previous) in zip(rows[1:], rows[:-1]):
        if value > 0 and previous > 0:
            returns.append((day, math.log(value / previous)))
    output: list[tuple[str, float]] = []
    for index in range(window - 1, len(returns)):
        sample = [value for _, value in returns[index - window + 1:index + 1]]
        mean = sum(sample) / len(sample)
        variance = sum((value - mean) ** 2 for value in sample) / max(1, len(sample) - 1)
        output.append((returns[index][0], math.sqrt(variance) * math.sqrt(252) * 100))
    return output


def _plausible_market_series(name: str, rows: list[tuple[str, float]]) -> bool:
    if len(rows) < 30:
        return False
    recent = [value for _, value in rows[-20:]]
    return all(within_band(name, value) for value in recent)


def estimate_direction(rows: list[tuple[str, float]], horizon: int = 5) -> MarketEstimate:
    """Empirical forward distribution; no random numbers and no fake precision."""
    if len(rows) < 80:
        return MarketEstimate(horizon, 0, 0, 0, 0, 0.0, False, "menos de 80 cierres observados")
    returns = [(rows[i][1] / rows[i - 1][1] - 1) * 100 for i in range(1, len(rows)) if rows[i - 1][1] > 0]
    current_momentum = sum(returns[-5:])
    current_vol = median(abs(value) for value in returns[-20:])
    outcomes: list[float] = []
    for index in range(25, len(rows) - horizon):
        history = returns[index - 5:index]
        volatility = median(abs(value) for value in returns[index - 20:index])
        # Similar regimes only. Wide bands prevent cherry-picking a tiny sample.
        if abs(sum(history) - current_momentum) <= max(1.5, current_vol * 4) and abs(volatility - current_vol) <= max(0.35, current_vol):
            outcomes.append((rows[index + horizon][1] / rows[index][1] - 1) * 100)
    if len(outcomes) < 30:
        return MarketEstimate(horizon, len(outcomes), 0, 0, 0, 0.0, False, "menos de 30 episodios históricos comparables")
    threshold = 0.5
    up = round(sum(value > threshold for value in outcomes) / len(outcomes) * 100)
    down = round(sum(value < -threshold for value in outcomes) / len(outcomes) * 100)
    flat = max(0, 100 - up - down)
    return MarketEstimate(horizon, len(outcomes), up, flat, down, median(outcomes), True, "distribución empírica de regímenes comparables")


def _source_line(
    labels: list[str], provider: str = "U.S. Energy Information Administration (EIA)",
    publication_note: str = "publicación oficial",
) -> str:
    if not labels:
        return f"Fuente: {provider}"
    period = labels[-1] if len(labels) == 1 else f"{labels[0]}–{labels[-1]}"
    return f"Fuente: {provider} · Periodo: {period}"


def _series_payload(
    raw_series: dict[str, list[tuple[str, float]]], *, title: str, subtitle: str,
    insight: str, decimals: int, kind: str = "line", prefix: str = "$", suffix: str = "",
    price_type: str | None = None,
) -> dict | None:
    series = {name: rows[-90:] for name, rows in raw_series.items() if len(rows) >= 2}
    if not series:
        return None
    labels = sorted({day for rows in series.values() for day, _ in rows})
    datasets, references = [], []
    for name, rows in series.items():
        by_date = dict(rows)
        datasets.append({"label": name, "color": MATTE.get(name, "#8A8A8A"), "values": [by_date.get(day) for day in labels], "pending": False})
        values = [value for _, value in rows]
        references.append({"label": name, "color": MATTE.get(name, "#8A8A8A"), "min": min(values), "max": max(values)})
    rendered_labels = [datetime.fromisoformat(day).strftime("%d/%m/%Y") for day in labels]
    return {
        "title": title,
        "subtitle": subtitle,
        "priceType": price_type,
        "labels": rendered_labels,
        "datasets": datasets,
        "annotations": [],
        "references": references,
        "chartKind": kind,
        "stepped": kind == "step",
        "insight": insight,
        "source": _source_line(rendered_labels),
        "credit": own_data_label(),
        "displayDecimals": decimals,
        "valuePrefix": prefix,
        "valueSuffix": suffix,
    }


def build_market_payload(raw_series: dict[str, list[tuple[str, float]]]) -> dict | None:
    insight = "Solo hay una referencia disponible; no se calcula el diferencial."
    if "WTI" in raw_series and "Brent" in raw_series:
        wti = dict(raw_series["WTI"])
        brent = dict(raw_series["Brent"])
        common = sorted(wti.keys() & brent.keys())
        if common:
            day = common[-1]
            spread = brent[day] - wti[day]
            insight = (
                f"Diferencial Brent–WTI del {datetime.fromisoformat(day):%d/%m/%Y}: "
                f"${spread:+.2f}/barril."
            )
    return _series_payload(
        raw_series, title="Petróleo: WTI y Brent",
        subtitle="Últimas 90 sesiones · cierres diarios observados · USD por barril",
        insight=insight, decimals=2, kind="line",
        price_type="MERCADO",
    )


def build_refined_products_payload(raw_series: dict[str, list[tuple[str, float]]]) -> dict | None:
    return _series_payload(
        raw_series, title="Gasolina y diésel mayoristas de EE. UU.",
        subtitle="Últimas 90 sesiones · futuros RBOB y ULSD · USD por galón",
        insight="Últimos cierres observados de gasolina RBOB y diésel ULSD.",
        decimals=3, kind="line", price_type="MERCADO",
    )


def build_brent_wti_spread_payload(raw_series: dict[str, list[tuple[str, float]]]) -> dict | None:
    """Compare only closes from the same date; never join unmatched sessions."""
    wti = dict(raw_series.get("WTI", []))
    brent = dict(raw_series.get("Brent", []))
    common = sorted(wti.keys() & brent.keys())
    if len(common) < 2:
        return None
    spread = [(day, brent[day] - wti[day]) for day in common]
    return _series_payload(
        {"Brent menos WTI": spread},
        title="Diferencial Brent–WTI",
        subtitle="Cierres de la misma fecha · USD por barril",
        insight=f"Último diferencial: ${spread[-1][1]:+.2f}/barril.",
        decimals=2,
    )


def build_indexed_energy_payload(raw_series: dict[str, list[tuple[str, float]]]) -> dict | None:
    """Normalize different units to a shared start without mixing price levels."""
    valid = {name: dict(raw_series[name]) for name in ("WTI", "Brent", "RBOB", "ULSD") if raw_series.get(name)}
    if len(valid) < 2:
        return None
    common = sorted(set.intersection(*(set(rows) for rows in valid.values())))[-60:]
    if len(common) < 2:
        return None
    indexed = {
        name: [(day, 100 * rows[day] / rows[common[0]]) for day in common]
        for name, rows in valid.items() if rows[common[0]] > 0
    }
    if len(indexed) < 2:
        return None
    return _series_payload(
        indexed,
        title="Energía: evolución relativa",
        subtitle=f"Base 100 el {datetime.fromisoformat(common[0]):%d/%m/%Y} · cierres comparables",
        insight="Evolución porcentual de cada serie desde una base común de 100.",
        decimals=1, prefix="", suffix="",
    )


def build_volatility_payload(raw_series: dict[str, list[tuple[str, float]]]) -> dict | None:
    volatility = {name: _rolling_volatility(rows) for name, rows in raw_series.items()}
    return _series_payload(
        volatility, title="Volatilidad realizada del petróleo",
        subtitle="Ventana móvil de 20 sesiones · anualizada · porcentaje",
        insight="Variación anualizada calculada con los últimos 20 cierres.",
        decimals=1, kind="area", prefix="", suffix="%", price_type="MERCADO",
    )


def build_weekly_moves_payload(raw_series: dict[str, list[tuple[str, float]]]) -> dict | None:
    names, values = [], []
    for name in ("WTI", "Brent", "RBOB", "ULSD"):
        change = _change(raw_series.get(name, []), 5)
        if change is not None:
            names.append(name)
            values.append(change)
    if not names:
        return None
    observed_days = sorted(
        {day for name in names for day, _ in raw_series.get(name, [])[-6:]}
    )
    rendered_days = [datetime.fromisoformat(day).strftime("%d/%m/%Y") for day in observed_days]
    return {
        "title": "Movimiento semanal de energía",
        "subtitle": "Cambio porcentual en las últimas cinco sesiones · bases comparables",
        "priceType": "MERCADO",
        "labels": names,
        "datasets": [{"label": "Variación", "color": "#8D887F", "values": values, "pending": False, "colorsBySign": True}],
        "annotations": [],
        "zeroBaseline": True,
        "references": [],
        "chartKind": "bar",
        "insight": "Cambio porcentual acumulado durante las últimas cinco sesiones.",
        "source": _source_line(rendered_days),
        "credit": own_data_label(),
        "displayDecimals": 2,
        "valuePrefix": "",
        "valueSuffix": "%",
    }


def build_us_retail_payload(raw_series: dict[str, list[tuple[str, float]]]) -> dict | None:
    series = {name: rows[-52:] for name, rows in raw_series.items() if len(rows) >= 2}
    if not series:
        return None
    labels = sorted({day for rows in series.values() for day, _ in rows})
    datasets, references = [], []
    for name, rows in series.items():
        by_date = dict(rows)
        color = RETAIL_COLORS.get(name, "#8A8A8A")
        datasets.append({"label": name, "color": color, "values": [by_date.get(day) for day in labels], "pending": False})
        values = [value for _, value in rows]
        references.append({"label": name, "color": color, "min": min(values), "max": max(values)})
    rendered = [datetime.fromisoformat(day).strftime("%d/%m/%Y") for day in labels]
    return {
        "title": "Precios minoristas de combustible en EE. UU.",
        "subtitle": "Promedios nacionales semanales con impuestos · USD por galón",
        "labels": rendered,
        "datasets": datasets,
        "annotations": [],
        "references": references,
        "chartKind": "line",
        "stepped": False,
        "insight": "Encuesta oficial semanal de la EIA · promedios nacionales con impuestos.",
        "source": _source_line(
            rendered,
            "U.S. Energy Information Administration (EIA)",
            "publicación semanal",
        ),
        "credit": own_data_label(),
        "displayDecimals": 3,
        "valuePrefix": "$",
        "valueSuffix": "",
    }


INVENTORY_COLORS = {
    "Crudo": "#C69236",
    "Gasolina": "#A88452",
    "Diésel": "#6C8FB3",
}


def _weekly_change(rows: list[tuple[str, float]]) -> float | None:
    if len(rows) >= 2 and rows[-2][1]:
        return (rows[-1][1] - rows[-2][1]) / rows[-2][1] * 100
    return None


def inventory_is_notable(rows_by_name: dict[str, list[tuple[str, float]]], threshold_percent: float = 1.5) -> bool:
    """Publish the weekly inventory only when a real week-over-week move happened.

    A calm feed does not repeat a flat series every Wednesday. The threshold is
    explicit and applied to observed values only.
    """
    biggest = 0.0
    for rows in rows_by_name.values():
        change = _weekly_change(rows)
        if change is not None:
            biggest = max(biggest, abs(change))
    return biggest >= threshold_percent


def build_us_inventory_payload(rows_by_name: dict[str, list[tuple[str, float]]]) -> dict | None:
    """Official weekly U.S. petroleum stocks; measured, never fabricated."""
    series = {name: rows[-52:] for name, rows in rows_by_name.items() if len(rows) >= 2}
    if not series:
        return None
    labels = sorted({day for rows in series.values() for day, _ in rows})
    datasets, references = [], []
    for name, rows in series.items():
        by_date = dict(rows)
        color = INVENTORY_COLORS.get(name, "#8A8A8A")
        datasets.append({"label": name, "color": color, "values": [by_date.get(day) for day in labels], "pending": False})
        values = [value for _, value in rows]
        references.append({"label": name, "color": color, "min": min(values), "max": max(values)})
    rendered = [datetime.fromisoformat(day).strftime("%d/%m/%Y") for day in labels]
    changes = "; ".join(
        f"{name} {change:+.1f}% sem."
        for name, rows in series.items()
        if (change := _weekly_change(rows)) is not None
    )
    return {
        "title": "Inventarios semanales de petróleo en EE. UU.",
        "subtitle": "Reporte oficial semanal (WPSR) · miles de barriles · crudo, gasolina y diésel",
        "labels": rendered,
        "datasets": datasets,
        "annotations": [],
        "references": references,
        "chartKind": "line",
        "stepped": False,
        "insight": (
            (changes or "Sin variación comparable semanal.")
            + " Variación semanal medida; es costo aguas arriba, no el precio local de Ecuador."
        ),
        "source": _source_line(
            rendered, "U.S. Energy Information Administration (EIA)", "publicación semanal"
        ),
        "credit": own_data_label(),
        "displayDecimals": 0,
        "valuePrefix": "",
        "valueSuffix": " mil bbl",
    }


def _month_label(period: str) -> str:
    try:
        return datetime.fromisoformat(f"{period}-01").strftime("%m/%Y")
    except ValueError:
        return period


def build_ecuador_production_payload(rows: list[tuple[str, float]]) -> dict | None:
    """Official monthly Ecuador crude oil production; never a fabricated series."""
    series = [(period, value) for period, value in rows if value and value > 0][-60:]
    if len(series) < 2:
        return None
    labels = [_month_label(period) for period, _ in series]
    values = [round(value) for _, value in series]
    return {
        "title": "Producción de petróleo crudo en Ecuador",
        "subtitle": "Promedio mensual oficial · miles de barriles por día (mil b/d)",
        "labels": labels,
        "datasets": [{"label": "Ecuador", "color": "#C69236", "values": values, "pending": False}],
        "annotations": [],
        "references": [{"label": "Ecuador", "color": "#C69236", "min": min(values), "max": max(values)}],
        "chartKind": "line",
        "stepped": False,
        "insight": f"Último mes publicado: {labels[-1]} con {values[-1]:.0f} mil b/d.",
        "source": (
            "Fuente: U.S. Energy Information Administration (EIA) · "
            f"Periodo: {labels[0]}–{labels[-1]}"
        ),
        "credit": own_data_label(),
        "displayDecimals": 0,
        "valuePrefix": "",
        "valueSuffix": " mil b/d",
    }


def build_ecuador_crude_exports_payload(rows: list[tuple[str, float]]) -> dict | None:
    """Official monthly Ecuador crude oil export value (USD FOB); never invented."""
    series = [(period, value) for period, value in rows if value and value > 0][-24:]
    if len(series) < 2:
        return None
    labels = [_month_label(period) for period, _ in series]
    values = [round(value) for _, value in series]
    return {
        "title": "Exportaciones de crudo de Ecuador",
        "subtitle": "Valor mensual oficial · millones de USD FOB (Banco Central del Ecuador)",
        "labels": labels,
        "datasets": [{"label": "Crudo", "color": "#8D887F", "values": values, "pending": False}],
        "annotations": [],
        "references": [{"label": "Crudo", "color": "#8D887F", "min": min(values), "max": max(values)}],
        "chartKind": "bar",
        "stepped": False,
        "insight": f"Último mes publicado: {labels[-1]} con {values[-1]:.0f} millones de USD FOB.",
        "source": (
            "Fuente: Banco Central del Ecuador (BCE) · "
            f"Periodo: {labels[0]}–{labels[-1]}"
        ),
        "credit": own_data_label(),
        "displayDecimals": 0,
        "valuePrefix": "$",
        "valueSuffix": " M",
    }


def build_crack_spread_payload(raw_series: dict[str, list[tuple[str, float]]]) -> dict | None:
    """US refining margin as an honest proxy for the cost of importing refined fuel.

    Gasoline crack = RBOB (USD/galón) × 42 − WTI (USD/barril); diesel crack =
    ULSD × 42 − WTI. Only dates where both legs exist are joined. It is a proxy:
    it is not Ecuador's real import cost.
    """
    wti = dict(raw_series.get("WTI", []))
    series: dict[str, list[tuple[str, float]]] = {}
    excluded: list[str] = []
    for name, product, metric in (
        ("Crack gasolina", "RBOB", "crack_gasolina"),
        ("Crack diésel", "ULSD", "crack_diesel"),
    ):
        leg = dict(raw_series.get(product, []))
        common = sorted(wti.keys() & leg.keys())[-60:]
        computed = [(day, leg[day] * GALLONS_PER_BARREL - wti[day]) for day in common]
        # A crack outside the historically sane band is far more likely a bad
        # quote than a real margin, so the gate keeps it out of the chart.
        gate = quarantine(metric, computed)
        if len(gate.kept) >= 2:
            series[name] = gate.kept
        if gate.removed:
            excluded.append(f"{name} excluido: {gate.reasons()}")
    if not series:
        return None
    insight = "; ".join(
        f"{name} al {datetime.fromisoformat(rows[-1][0]):%d/%m/%Y}: ${rows[-1][1]:.2f}/barril"
        for name, rows in series.items()
    ) + "."
    if excluded:
        insight += " " + " ".join(excluded) + "."
    payload = _series_payload(
        series,
        title="Margen de refino (crack spread) de referencia",
        subtitle="RBOB y ULSD contra WTI · proxy del costo de importar refinados · USD por barril",
        insight=insight,
        decimals=2, prefix="$", suffix="/bbl",
    )
    if payload:
        payload["zeroBaseline"] = True
    return payload


def _leg_plausible(
    wti: list[tuple[str, float]], product_rows: list[tuple[str, float]], metric: str
) -> bool:
    """Cross-check a product quote against WTI through its crack.

    The absolute band alone cannot catch a bad refined quote, because the raw
    per-gallon range is wide.  The implied crack is far more sensitive: if it
    falls outside the sane refining-margin band, the quote is not trusted.  When
    there is no WTI to cross-check against, the absolute band stands alone.
    """
    product = dict(product_rows)
    crude = dict(wti)
    common = sorted(product.keys() & crude.keys())
    if not common:
        return True
    return within_band(metric, product[common[-1]] * GALLONS_PER_BARREL - crude[common[-1]])


def build_price_gap_payload(
    official_prices: dict[str, float], raw_series: dict[str, list[tuple[str, float]]]
) -> dict | None:
    """Measured gap between Ecuador's regulated price and the international product.

    Both sides are USD per gallon, so it is an honest same-unit comparison. It
    adds no freight, taxes, margins or subsidies and therefore is NOT a computed
    fiscal-gap figure; the label says so. Published only when both sides exist.
    """
    gasoline = next(
        (official_prices[key] for key in ("Extra", "Súper", "Super", "Premium", "Gasolina") if official_prices.get(key)),
        None,
    )
    diesel = next(
        (official_prices[key] for key in ("Diésel", "Diesel") if official_prices.get(key)),
        None,
    )
    wti = raw_series.get("WTI", [])
    rbob = quarantine("RBOB", raw_series.get("RBOB", [])).kept
    ulsd = quarantine("ULSD", raw_series.get("ULSD", [])).kept
    categories: list[str] = []
    regulated: list[float] = []
    reference: list[float] = []
    excluded: list[str] = []
    if gasoline and rbob and _leg_plausible(wti, rbob, "crack_gasolina"):
        categories.append("Gasolina")
        regulated.append(round(float(gasoline), 2))
        reference.append(round(rbob[-1][1], 2))
    elif gasoline and rbob:
        excluded.append("Gasolina excluida: la cotización internacional quedó fuera de rango")
    if diesel and ulsd and _leg_plausible(wti, ulsd, "crack_diesel"):
        categories.append("Diésel")
        regulated.append(round(float(diesel), 2))
        reference.append(round(ulsd[-1][1], 2))
    elif diesel and ulsd:
        excluded.append("Diésel excluido: la cotización internacional quedó fuera de rango")
    if not categories:
        return None
    detail = "; ".join(
        f"{name}: oficial ${reg:.2f}/gal vs referencia ${ref:.2f}/gal ({reg - ref:+.2f})"
        for name, reg, ref in zip(categories, regulated, reference)
    )
    if excluded:
        detail += ". " + ". ".join(excluded)
    return {
        "title": "Brecha de referencia del precio de combustible",
        "subtitle": "Precio oficial de Ecuador frente a la cotización internacional del producto · USD por galón",
        "labels": categories,
        "datasets": [
            {"label": "Oficial Ecuador", "color": "#C69236", "values": regulated, "pending": False},
            {"label": "Referencia internacional", "color": "#6C8FB3", "values": reference, "pending": False},
        ],
        "annotations": [],
        "references": [
            {"label": "Oficial Ecuador", "color": "#C69236", "min": min(regulated), "max": max(regulated)},
            {"label": "Referencia internacional", "color": "#6C8FB3", "min": min(reference), "max": max(reference)},
        ],
        "chartKind": "bar",
        "zeroBaseline": True,
        "insight": detail + ". Diferencia medida; no incluye impuestos, subsidios, flete ni márgenes.",
        "source": "Fuente: precio oficial de Ecuador + cotizaciones internacionales (U.S. EIA)",
        "credit": own_data_label(),
        "displayDecimals": 2,
        "valuePrefix": "$",
        "valueSuffix": "/gal",
    }


def build_market_heatmap_payload(raw_series: dict[str, list[tuple[str, float]]]) -> dict | None:
    rows = []
    for name in ("WTI", "Brent", "RBOB", "ULSD"):
        series = raw_series.get(name, [])
        if series:
            rows.append({"label": name, "values": [_change(series, 1), _change(series, 5), _change(series, 20)]})
    if not rows:
        return None
    latest = max((series[-1][0] for series in raw_series.values() if series), default="sin fecha")
    return {
        "title": "Mapa de calor del mercado de combustibles",
        "subtitle": "Variación de cierres observados · verde sube, rojo baja",
        "rows": rows,
        "insight": "Rendimiento observado a 1, 5 y 20 sesiones.",
        "source": _source_line([datetime.fromisoformat(latest).strftime("%d/%m/%Y")]),
        "credit": own_data_label(),
    }


def _observed_sentence(name: str, rows: list[tuple[str, float]]) -> str:
    price = rows[-1][1]
    week = _change(rows, 5)
    per_gallon = name in {"RBOB", "ULSD", "Gasolina regular", "Gasolina premium", "Diésel"}
    unit = "USD/galón" if per_gallon else "USD/barril"
    decimals = 3 if per_gallon else 2
    cadence = "cinco publicaciones" if name not in {"RBOB", "ULSD", "WTI", "Brent"} else "cinco sesiones"
    move = "sin comparación suficiente" if week is None else f"{week:+.2f}% en {cadence}"
    return f"{name}: ${price:.{decimals}f} {unit}; {move}."


def build_quantitative_reading(raw_series: dict[str, list[tuple[str, float]]]) -> list[str]:
    """Measured finance the bot applies to the series. Never a direction, never a forecast."""
    reading: list[str] = []
    crude = raw_series.get("WTI") or raw_series.get("Brent")
    if not crude:
        return reading
    name = "WTI" if raw_series.get("WTI") else "Brent"
    volatility = realized_volatility(crude, 20)
    if volatility is not None:
        rank = percentile_rank(volatility_series(crude, 20), volatility)
        tail = f", percentil {rank} del histórico observado" if rank is not None else ""
        reading.append(f"Volatilidad realizada 20 sesiones de {name}: {volatility:.1f}% anualizada{tail}.")
    position = percentile_rank(crude)
    if position is not None:
        reading.append(f"El último cierre de {name} está en el percentil {position} de su histórico observado.")
    for product in ("RBOB", "ULSD"):
        leg = raw_series.get(product)
        if leg:
            correlation = return_correlation(leg, crude, 60)
            if correlation is not None:
                reading.append(f"Correlación de retornos 60 sesiones {product}–{name}: {correlation:+.2f}.")
    return reading


def build_market_sections(
    raw_series: dict[str, list[tuple[str, float]]], related_news: list[dict] | None = None,
    us_retail: dict[str, list[tuple[str, float]]] | None = None,
) -> TelegramSections:
    present = [(name, raw_series[name]) for name in ("RBOB", "ULSD", "Brent", "WTI") if raw_series.get(name)]
    latest = max((rows[-1][0] for _, rows in present), default="")
    short_values = []
    for name, rows in present:
        decimals = 3 if name in {"RBOB", "ULSD"} else 2
        unit = "/gal" if name in {"RBOB", "ULSD"} else "/barril"
        week = _change(rows, 5)
        short_values.append(f"{name} ${rows[-1][1]:.{decimals}f}{unit}" + (f" ({week:+.1f}% sem.)" if week is not None else ""))
    retail_present = [(name, rows) for name, rows in (us_retail or {}).items() if rows]
    if retail_present:
        retail_latest = max(rows[-1][0] for _, rows in retail_present)
        retail_values = " | ".join(f"{name} ${rows[-1][1]:.3f}/gal" for name, rows in retail_present)
        squawk = trim_x(
            f"EE. UU. | Precios minoristas al {datetime.fromisoformat(retail_latest):%d/%m/%Y}: "
            f"{retail_values}. Fuente oficial: EIA.", 300,
        )
    else:
        same_date = all(rows[-1][0] == latest for _, rows in present)
        if not same_date:
            short_values = [
                f"{name} ${rows[-1][1]:.{3 if name in {'RBOB', 'ULSD'} else 2}f}"
                f"/{'gal' if name in {'RBOB', 'ULSD'} else 'barril'}"
                f" ({datetime.fromisoformat(rows[-1][0]):%d/%m})"
                for name, rows in present
            ]
        squawk = trim_x(
            f"Mercado energético | {'Cierres al ' + datetime.fromisoformat(latest).strftime('%d/%m/%Y') if same_date else 'Últimos cierres disponibles'}: "
            + " | ".join(short_values)
            + ". Fuente oficial: U.S. EIA.",
            300,
        ) if latest else "Mercado energético | No hay cierres suficientes para publicar."

    lines = ["ANÁLISIS EN LENGUAJE CLARO", "", "Qué pasó en el mercado:"]
    if retail_present:
        lines.append("EE. UU. (promedio minorista oficial EIA):")
        lines.extend(_observed_sentence(name, rows) for name, rows in retail_present)
        lines.append("Estas cifras incluyen impuestos y se publican semanalmente; no son datos en tiempo real.")
        lines.append("")
        lines.append("Referencias mayoristas y de crudo:")
    lines.extend(_observed_sentence(name, rows) for name, rows in present)
    strongest = max(
        ((name, _change(rows, 5)) for name, rows in present if _change(rows, 5) is not None),
        key=lambda item: abs(item[1]), default=None,
    )
    if strongest:
        lines.append(f"Lo más importante: la mayor variación semanal fue {strongest[0]} ({strongest[1]:+.2f}%).")
    lines.extend(["", "Noticias que pueden estar relacionadas:"])
    if related_news:
        for story in related_news[:2]:
            lines.append(f"Según {story['source_name']}: {story['title']}")
            lines.append(story["canonical_url"])
        lines.append("Relación temporal; no causalidad confirmada sin evidencia explícita.")
    else:
        lines.append("No hay una noticia reciente verificada que explique por sí sola el movimiento.")
    anomalies = detect_anomalies(raw_series)
    if anomalies:
        lines.extend(["", "Alerta estadística:", *[signal.explanation for signal in anomalies[:2]]])
    reading = build_quantitative_reading(raw_series)
    if reading:
        lines.extend(["", "Lectura cuantitativa (descriptiva, sin pronóstico):", *reading])
    lines.extend([
        "", "Lectura para Ecuador:",
        "RBOB y ULSD son alertas externas de costos. El precio local solo cambia cuando una fuente ecuatoriana verificable publica la decisión y su vigencia.",
        "El mercado internacional no es el precio oficial de Ecuador.",
        f"Último cierre observado: {datetime.fromisoformat(latest):%d/%m/%Y}. Verificado en hora de Ecuador (UTC−5)." if latest else "Sin fecha de cierre verificable.",
    ])

    estimates = [(name, estimate_direction(rows)) for name, rows in present if name in {"RBOB", "ULSD"}]
    publishable = [(name, result) for name, result in estimates if result.publishable]
    if publishable:
        forecast_lines = ["ESTIMACIÓN ESTADÍSTICA — NO ES DATO OFICIAL", "Horizonte: próximas cinco sesiones."]
        for name, result in publishable:
            forecast_lines.append(
                f"{name}: sube {result.probability_up}% · estable {result.probability_flat}% · baja {result.probability_down}% "
                f"({result.sample_size} episodios comparables; mediana {result.median_change:+.2f}%)."
            )
        forecast_lines.append("No predice el precio de Ecuador y no debe publicarse como certeza.")
        forecast = "\n".join(forecast_lines)
    else:
        forecast = "ESTIMACIÓN ESTADÍSTICA\nNo se publica: falta una muestra histórica comparable suficiente."
    return TelegramSections(squawk=squawk, context="\n".join(lines), forecast=forecast)


def build_market_briefing(raw_series: dict[str, list[tuple[str, float]]], related_news: list[dict] | None = None) -> str:
    """Compatibility text for logs and audits; Telegram uses separate messages."""
    sections = build_market_sections(raw_series, related_news)
    return "\n\n".join(sections.messages())


def _snapshot_path() -> Path:
    return Path(os.getenv("FUELINTEL_DATA_DIR", "./data")) / "market_snapshot.json"


def _load_snapshot() -> dict[str, tuple[str, list[tuple[str, float]]]]:
    """Last successful fetch per series: (fetched_local_iso, rows).

    Lives inside FUELINTEL_DATA_DIR, which the workflow restores from the
    fuelintel-state cache, so the keyless fallback survives across runs and a
    provider outage degrades to the last honest snapshot instead of blank charts.
    """
    try:
        payload = json.loads(_snapshot_path().read_text("utf-8"))
    except Exception:
        return {}
    series: dict[str, tuple[str, list[tuple[str, float]]]] = {}
    if not isinstance(payload, dict):
        return series
    for name, block in payload.items():
        try:
            rows = [(str(d), float(v)) for d, v in block["rows"]]
            fetched = str(block.get("fetched", ""))
            if rows:
                series[name] = (fetched, sorted(set(rows)))
        except Exception:
            continue
    return series


def _save_snapshot(series: dict[str, tuple[str, list[tuple[str, float]]]]) -> None:
    payload = {}
    for name, (fetched, rows) in series.items():
        payload[name] = {"fetched": fetched, "rows": [list(row) for row in rows[-_MAX_CACHE_ROWS:]]}
    try:
        path = _snapshot_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
    except Exception:
        return


def load_market_series() -> dict[str, list[tuple[str, float]]]:
    """Resolve each official series with an ordered fallback ladder.

    1. EIA API (cuando hay EIA_API_KEY) · 2. .xls oficial de eia.gov/dnav (sin
    key) · 3. último snapshot sano en FUELINTEL_DATA_DIR. Nunca se rellenan
    huecos con supuestos: una serie sin datos frescos decae al snapshot, y si
    tampoco hay snapshot no se dibuja.
    """
    raw_series: dict[str, list[tuple[str, float]]] = {}
    snapshot = _load_snapshot()
    snapshot_changed = False
    now_iso = datetime.now(ECUADOR_TZ).isoformat(timespec="seconds")
    for name, series_id in EIA_SPOT_SERIES.items():
        rows: list[tuple[str, float]] = []
        from_cache = False
        for resolver in (partial(_history, series_id), partial(_history_dnav, name)):
            try:
                rows = resolver()  # type: ignore[assignment]
            except Exception:
                rows = []
            if rows:
                break
        if not rows and name in snapshot:
            rows, from_cache = snapshot[name][1], True
        if not rows:
            continue
        try:
            validate_market_series(name, rows)
        except Exception:
            continue
        if not _plausible_market_series(name, rows):
            continue
        raw_series[name] = rows
        if not from_cache:
            snapshot[name] = (now_iso, rows[-_MAX_CACHE_ROWS:])
            snapshot_changed = True
    if snapshot_changed:
        _save_snapshot(snapshot)
    return raw_series


def select_event_chart_key(text: str) -> str:
    """Choose a chart from the subject of the alert; AI never makes this decision."""
    folded = text.casefold()
    if any(term in folded for term in ("diferencial", "spread", "brent-wti", "brent wti")):
        return "spread"
    if any(term in folded for term in ("evolución relativa", "relative performance", "comparación relativa")):
        return "indexed"
    if any(term in folded for term in ("volatil", "volatility", "incertidumbre", "uncertainty", "risk spike")):
        return "volatility"
    if any(term in folded for term in ("gasoline", "gasolina", "diesel", "diésel", "refiner", "refinería", "rbob", "ulsd")):
        return "products"
    if any(term in folded for term in (
        "selloff", "rally", "surge", "plunge", "jump", "slump", "se desploma", "se dispara",
        "caída general", "subida general", "mercado energético",
    )):
        return "heatmap"
    if any(term in folded for term in ("semana", "weekly", "últimas sesiones", "last sessions")):
        return "weekly-moves"
    return "crude"


def choose_market_focus(
    raw_series: dict[str, list[tuple[str, float]]], related_news: list[dict] | None = None,
) -> str:
    """Pick the most explanatory daily view from news and measured market state."""
    if related_news:
        news_text = " ".join(str(story.get("title", "")) for story in related_news[:3])
        news_choice = select_event_chart_key(news_text)
        if news_choice != "crude" or any(
            term in news_text.casefold()
            for term in ("oil", "crude", "petróleo", "brent", "wti", "opep", "opec", "hormuz")
        ):
            return news_choice

    signals = detect_anomalies(raw_series)
    anomalous_groups = {
        "products" if signal.series in {"RBOB", "ULSD"} else "crude"
        for signal in signals
    }
    if len(anomalous_groups) > 1:
        return "heatmap"
    if "products" in anomalous_groups:
        return "products"
    if "crude" in anomalous_groups:
        return "weekly-moves"

    volatility_values = [
        value
        for name in ("WTI", "Brent")
        for _, value in _rolling_volatility(raw_series.get(name, []))[-1:]
    ]
    if volatility_values and max(volatility_values) >= 60:
        return "volatility"

    weekly_moves = [abs(value) for rows in raw_series.values() if (value := _change(rows, 5)) is not None]
    if weekly_moves and max(weekly_moves) >= 5:
        return "weekly-moves"

    if "WTI" in raw_series and "Brent" in raw_series:
        wti, brent = dict(raw_series["WTI"]), dict(raw_series["Brent"])
        common = sorted(wti.keys() & brent.keys())[-30:]
        spreads = [brent[day] - wti[day] for day in common]
        if len(spreads) >= 20:
            baseline = spreads[:-1]
            center = median(baseline)
            typical = median(abs(value - center) for value in baseline)
            if typical > 0 and abs(spreads[-1] - center) >= max(2.0, 3 * typical):
                return "spread"

    product_moves = [abs(value) for name in ("RBOB", "ULSD") if (value := _change(raw_series.get(name, []), 5)) is not None]
    crude_moves = [abs(value) for name in ("WTI", "Brent") if (value := _change(raw_series.get(name, []), 5)) is not None]
    if product_moves and crude_moves:
        if min(product_moves) > max(crude_moves) * 1.7 and min(product_moves) >= 1.5:
            return "indexed"
        if max(crude_moves) > max(product_moves) * 1.5:
            return "crude"
    return "products" if product_moves else "crude"


def generate_market_chart(destination: Path) -> Path | None:
    """Small smoke-test entry point used by CI without sending Telegram messages."""
    raw = load_market_series()
    payload = build_market_payload({key: value for key, value in raw.items() if key in {"WTI", "Brent"}})
    return render_chart(payload, destination) if payload else None


def generate_event_market_chart(text: str, destination: Path) -> Path | None:
    """Render one relevant context chart for a high-impact news alert."""
    raw = load_market_series()
    chart_key = select_event_chart_key(text)
    if chart_key == "products":
        payload = build_refined_products_payload({key: value for key, value in raw.items() if key in {"RBOB", "ULSD"}})
    elif chart_key == "volatility":
        payload = build_volatility_payload({key: value for key, value in raw.items() if key in {"WTI", "Brent"}})
    elif chart_key == "weekly-moves":
        payload = build_weekly_moves_payload(raw)
    elif chart_key == "spread":
        payload = build_brent_wti_spread_payload(raw)
    elif chart_key == "indexed":
        payload = build_indexed_energy_payload(raw)
    elif chart_key == "heatmap":
        payload = build_market_heatmap_payload(raw)
    else:
        payload = build_market_payload({key: value for key, value in raw.items() if key in {"WTI", "Brent"}})
    if payload is None:
        return None
    payload["insight"] = "Movimiento de mercado observado alrededor de la noticia seleccionada."
    return render_heatmap(payload, destination) if chart_key == "heatmap" else render_chart(payload, destination)


def generate_market_bundle(
    destination_dir: Path, related_news: list[dict] | None = None, warehouse_path: Path | None = None,
    eia_api_key: str = "", selected_only: bool = False, variety: bool = False,
    bce_exports: list[tuple[str, float]] | None = None,
    official_prices: dict[str, float] | None = None,
) -> tuple[list[tuple[str, Path, str]], TelegramSections | None, str, str]:
    raw_series = load_market_series()
    us_retail: dict[str, list[tuple[str, float]]] = {}
    ecuador_production: list[tuple[str, float]] = []
    us_inventory: dict[str, list[tuple[str, float]]] = {}
    if eia_api_key:
        from .eia import fetch_ecuador_crude_production, fetch_us_retail_prices, fetch_us_weekly_inventory
        try:
            us_retail = fetch_us_retail_prices(eia_api_key)
        except Exception:
            us_retail = {}
        try:
            ecuador_production = fetch_ecuador_crude_production(eia_api_key)
        except Exception:
            ecuador_production = []
        try:
            us_inventory = fetch_us_weekly_inventory(eia_api_key)
        except Exception:
            us_inventory = {}
    if warehouse_path is not None and raw_series:
        try:
            from .warehouse import save_market_history
            save_market_history(warehouse_path, raw_series)
        except Exception:
            pass
    focus = choose_market_focus(raw_series, related_news)
    specs = [
        ("crude", build_market_payload({k: v for k, v in raw_series.items() if k in {"WTI", "Brent"}})),
        ("products", build_refined_products_payload({k: v for k, v in raw_series.items() if k in {"RBOB", "ULSD"}})),
        ("volatility", build_volatility_payload({k: v for k, v in raw_series.items() if k in {"WTI", "Brent"}})),
        ("weekly-moves", build_weekly_moves_payload(raw_series)),
        ("us-retail", build_us_retail_payload(us_retail)),
        ("spread", build_brent_wti_spread_payload(raw_series)),
        ("indexed", build_indexed_energy_payload(raw_series)),
    ]
    retail_spec = next(((key, payload) for key, payload in specs if key == "us-retail" and payload), None)
    production_payload = build_ecuador_production_payload(ecuador_production)
    all_specs = list(specs)
    if selected_only and focus == "heatmap":
        specs = []
    elif selected_only:
        preferred = next(((key, payload) for key, payload in specs if key == focus and payload), None)
        fallback = next(((key, payload) for key, payload in specs if payload), None)
        specs = [preferred or fallback] if preferred or fallback else []
        if variety:
            # Add one complementary view per day so the summary stays varied
            # without flooding the operator: the rotation is date-driven and
            # never repeats the focus. AI never chooses this.
            chosen = {key for key, _ in specs}
            alternatives = [
                (key, payload)
                for key, payload in all_specs
                if payload and key not in chosen and key != "us-retail"
            ]
            if alternatives:
                offset = datetime.now(ECUADOR_TZ).timetuple().tm_yday % len(alternatives)
                specs.append(alternatives[offset])
    if selected_only and retail_spec and all(key != "us-retail" for key, _ in specs):
        # The official EIA weekly survey is an independent publication cadence;
        # its data signature prevents repeats when the survey has not changed.
        specs.append(retail_spec)
    if selected_only and production_payload and all(key != "ecuador-production" for key, _ in specs):
        # Monthly official publication; the signature prevents repeats.
        specs.append(("ecuador-production", production_payload))
    exports_payload = build_ecuador_crude_exports_payload(bce_exports or [])
    if selected_only and exports_payload and all(key != "ecuador-exports" for key, _ in specs):
        # Monthly official publication; the signature prevents repeats.
        specs.append(("ecuador-exports", exports_payload))
    crack_payload = build_crack_spread_payload(raw_series)
    if selected_only and crack_payload and all(key != "crack-spread" for key, _ in specs):
        # Daily-changing cost-of-importing proxy; the signature prevents repeats.
        specs.append(("crack-spread", crack_payload))
    gap_payload = build_price_gap_payload(official_prices or {}, raw_series)
    if selected_only and gap_payload and all(key != "price-gap" for key, _ in specs):
        # Changes only when the regulated price moves; the signature prevents repeats.
        specs.append(("price-gap", gap_payload))
    # The weekly US inventory is only published when the week-over-week move is
    # material, so the feed never repeats a flat series every Wednesday.
    if selected_only and inventory_is_notable(us_inventory) and all(key != "us-inventory" for key, _ in specs):
        inventory_payload = build_us_inventory_payload(us_inventory)
        if inventory_payload:
            specs.append(("us-inventory", inventory_payload))
    destination_dir.mkdir(parents=True, exist_ok=True)
    artifacts: list[tuple[str, Path, str]] = []
    for key, payload in specs:
        if not payload:
            continue
        stable = {name: payload.get(name) for name in ("labels", "datasets", "references", "insight", "chartKind")}
        signature = hashlib.sha256(json.dumps(stable, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        artifacts.append((key, render_chart(payload, destination_dir / f"market-{key}.png"), signature))
    heatmap = build_market_heatmap_payload(raw_series) if not selected_only or focus == "heatmap" else None
    if heatmap:
        signature = hashlib.sha256(json.dumps(heatmap["rows"], sort_keys=True).encode()).hexdigest()
        artifacts.append(("heatmap", render_heatmap(heatmap, destination_dir / "market-heatmap.png"), signature))
    latest = max(
        (rows[-1][0] for rows in (*raw_series.values(), *us_retail.values()) if rows),
        default="",
    )
    return artifacts, build_market_sections(raw_series, related_news, us_retail) if raw_series or us_retail else None, focus, latest
