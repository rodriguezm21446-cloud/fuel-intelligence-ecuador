from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from .chart_renderer import render_chart, render_heatmap
from .charts import own_data_label
from .anomalies import detect_anomalies


def _history(symbol: str, days: int = 180) -> list[tuple[str, float]]:
    start = (date.today() - timedelta(days=days)).isoformat()
    try:
        from openbb import obb

        output = obb.derivatives.futures.historical(symbol=symbol, start_date=start, provider="yfinance")
        frame = output.to_df().reset_index()
    except Exception:
        # OpenBB remains the primary data layer. This provider-level fallback keeps
        # the close package alive if an OpenBB extension fails to initialize.
        import yfinance as yf

        cache_dir = Path(os.getenv("FUELINTEL_DATA_DIR", "data/runtime")) / "yfinance-cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        yf.set_tz_cache_location(str(cache_dir))
        frame = yf.Ticker(symbol).history(start=start, auto_adjust=False).reset_index()
    by_date: dict[str, float] = {}
    for _, row in frame.iterrows():
        when = row.get("date", row.get("Date", row.iloc[0]))
        close = row.get("close", row.get("Close"))
        if close is not None:
            day, value = str(when)[:10], float(close)
            if day <= date.today().isoformat() and math.isfinite(value) and 1 <= value <= 500:
                by_date[day] = value
    return sorted(by_date.items())


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
    return (rows[-1][1] / rows[-1 - sessions][1] - 1) * 100


def _rolling_volatility(rows: list[tuple[str, float]], window: int = 20) -> list[tuple[str, float]]:
    """Annualized realized volatility from observed daily log returns."""
    returns: list[tuple[str, float]] = []
    for (day, value), (_, previous) in zip(rows[1:], rows[:-1]):
        if value > 0 and previous > 0:
            returns.append((day, math.log(value / previous)))
    output: list[tuple[str, float]] = []
    for index in range(window - 1, len(returns)):
        sample = [value for _, value in returns[index - window + 1:index + 1]]
        mean = sum(sample) / len(sample)
        variance = sum((value - mean) ** 2 for value in sample) / (len(sample) - 1)
        output.append((returns[index][0], math.sqrt(variance) * math.sqrt(252) * 100))
    return output


def _plausible_market_series(name: str, rows: list[tuple[str, float]]) -> bool:
    """Reject ticker collisions and unit errors before charts or signals are built."""
    if len(rows) < 30:
        return False
    recent = [value for _, value in rows[-20:]]
    low, high = ((20.0, 250.0) if name in {"WTI", "Brent"} else (0.5, 10.0))
    return all(low <= value <= high for value in recent)


def _line_payload(
    raw_series: dict[str, list[tuple[str, float]]],
    *,
    title: str,
    subtitle: str,
    colors: dict[str, str],
    insight: str,
    decimals: int,
    prefix: str = "$",
    suffix: str = "",
) -> dict | None:
    series = {name: rows[-90:] for name, rows in raw_series.items() if len(rows) >= 2}
    if not series:
        return None
    labels = sorted({day for rows in series.values() for day, _ in rows})
    datasets, references = [], []
    for name, rows in series.items():
        by_date = dict(rows)
        color = colors.get(name, "#8A8A8A")
        datasets.append({"label": name, "color": color, "values": [by_date.get(day) for day in labels], "pending": False})
        values = [value for _, value in rows]
        references.append({"label": name, "color": color, "min": min(values), "max": max(values)})
    verified = datetime.now(timezone.utc).astimezone(ZoneInfo("America/Guayaquil"))
    return {
        "title": title,
        "subtitle": subtitle,
        "labels": [datetime.fromisoformat(day).strftime("%d/%m/%Y") for day in labels],
        "datasets": datasets,
        "annotations": [],
        "references": references,
        "stepped": False,
        "insight": insight,
        "source": f"Fuente: OpenBB Platform · Proveedor: Yahoo Finance · Verificado: {verified:%d/%m/%Y %H:%M} · hora de Ecuador (UTC−5)",
        "credit": own_data_label(),
        "displayDecimals": decimals,
        "valuePrefix": prefix,
        "valueSuffix": suffix,
    }


def build_market_briefing(
    raw_series: dict[str, list[tuple[str, float]]],
    related_news: list[dict] | None = None,
) -> str:
    """Plain-language result; the quantitative work stays behind the message."""
    labels = {
        "WTI": "WTI (USD/barril)",
        "Brent": "Brent (USD/barril)",
        "RBOB": "Gasolina RBOB (USD/galón)",
        "ULSD": "Diésel/ULSD (USD/galón)",
    }
    observed: dict[str, tuple[float, float | None, float | None, float | None]] = {}
    signals: list[tuple[float, float]] = []
    weights = {"WTI": 0.10, "Brent": 0.20, "RBOB": 0.35, "ULSD": 0.35}
    for name in ("WTI", "Brent", "RBOB", "ULSD"):
        rows = raw_series.get(name, [])
        if not rows:
            continue
        week = _change(rows, 5)
        month = _change(rows, 20)
        if week is not None:
            signals.append((max(-15.0, min(15.0, week)), weights[name]))
        weekly_dollars = rows[-1][1] - rows[-6][1] if len(rows) > 5 else None
        observed[name] = (rows[-1][1], week, month, weekly_dollars)

    lines = ["Qué pasó en el mercado", ""]
    weekly = [(name, values[1]) for name, values in observed.items() if values[1] is not None]
    if weekly:
        strongest_name, strongest_change = max(weekly, key=lambda item: abs(item[1]))
        _, _, _, absolute = observed[strongest_name]
        verb = "subió" if strongest_change >= 0 else "bajó"
        unit = "por galón" if strongest_name in {"RBOB", "ULSD"} else "por barril"
        decimals = 3 if strongest_name in {"RBOB", "ULSD"} else 2
        lines.append(f"Lo más importante: {labels[strongest_name]} {verb} ${abs(absolute or 0):.{decimals}f} {unit} en las últimas cinco sesiones.")
    for name in ("RBOB", "ULSD", "Brent", "WTI"):
        if name not in observed:
            continue
        value, week, _, absolute = observed[name]
        unit_decimals = 3 if name in {"RBOB", "ULSD"} else 2
        move = "sin comparación semanal" if absolute is None else f"{absolute:+.2f} USD en una semana"
        lines.append(f"{labels[name]} cerró en ${value:.{unit_decimals}f}; cambió {move}.")

    if signals:
        weight_sum = sum(weight for _, weight in signals)
        score = sum(value * weight for value, weight in signals) / weight_sum
        if score >= 2:
            direction = "presión alcista"
        elif score <= -2:
            direction = "presión bajista"
        else:
            direction = "presión mixta o neutral"
        lines.extend(
            [
                "",
                "Qué vigilar:",
                f"En conjunto, las referencias internacionales muestran {direction} a corto plazo.",
                "Esto no es el precio oficial de Ecuador ni permite afirmar todavía cuánto cambiará.",
            ]
        )
    else:
        lines.extend(["", "Qué vigilar:", "Faltan datos; no se emite señal."])
    lines.extend(["", "Noticias que pueden estar relacionadas:"])
    if related_news:
        for story in related_news[:2]:
            lines.append(f"• Según {story['source_name']}: {story['title']}")
            lines.append(story["canonical_url"])
        lines.append("Relación temporal, no causalidad confirmada. El bot no atribuye el movimiento sin evidencia explícita.")
    else:
        lines.append("No hay una noticia reciente verificada que explique por sí sola el movimiento.")
    anomalies = detect_anomalies(raw_series)
    lines.extend(["", "Cambios fuera de lo habitual:"])
    if anomalies:
        lines.extend(f"• {signal.explanation}" for signal in anomalies[:3])
        lines.append("Es una alerta estadística, no una predicción ni una causa comprobada.")
    else:
        lines.append("No se detectó un movimiento extremo con el historial reciente disponible.")
    lines.extend([
        "",
        "Qué significa para Ecuador:",
        "RBOB y ULSD sirven como alerta externa. Solo ARCH o Petroecuador confirman el precio local mediante su documento oficial.",
        "Hora de Ecuador (UTC−5).",
    ])
    return "\n".join(lines)


def build_market_heatmap_payload(raw_series: dict[str, list[tuple[str, float]]]) -> dict | None:
    rows = []
    for name in ("WTI", "Brent", "RBOB", "ULSD"):
        series = raw_series.get(name, [])
        if series:
            rows.append({"label": name, "values": [_change(series, 1), _change(series, 5), _change(series, 20)]})
    if not rows:
        return None
    verified = datetime.now(timezone.utc).astimezone(ZoneInfo("America/Guayaquil"))
    return {
        "title": "Mapa de calor del mercado de combustibles",
        "subtitle": "Variación de cierres observados · verde sube, rojo baja",
        "rows": rows,
        "insight": "Compara rendimientos porcentuales; no mezcla USD por barril con USD por galón.",
        "source": f"Fuente: OpenBB Platform · Proveedor: Yahoo Finance · Verificado: {verified:%d/%m/%Y %H:%M} · hora de Ecuador (UTC−5)",
        "credit": own_data_label(),
    }


def build_market_payload(raw_series: dict[str, list[tuple[str, float]]]) -> dict | None:
    if not raw_series:
        return None
    insight = "Solo hay una referencia disponible; no se calcula el diferencial."
    if "WTI" in raw_series and "Brent" in raw_series:
        spread = raw_series["Brent"][-1][1] - raw_series["WTI"][-1][1]
        insight = f"Diferencial Brent–WTI: ${spread:+.2f} por barril. No implica por sí solo un cambio en Ecuador."
    return _line_payload(
        raw_series, title="Petróleo: WTI y Brent",
        subtitle="Últimas 90 sesiones · cierres diarios observados · USD por barril",
        colors={"WTI": "#FF9D00", "Brent": "#E8E8E8"}, insight=insight, decimals=2,
    )


def build_refined_products_payload(raw_series: dict[str, list[tuple[str, float]]]) -> dict | None:
    insight = "RBOB aproxima gasolina mayorista; ULSD aproxima diésel. No son precios oficiales de Ecuador."
    return _line_payload(
        raw_series, title="Gasolina y diésel mayoristas de EE. UU.",
        subtitle="Últimas 90 sesiones · RBOB y ULSD · USD por galón",
        colors={"RBOB": "#FF9D00", "ULSD": "#4A90D9"}, insight=insight, decimals=3,
    )


def build_volatility_payload(raw_series: dict[str, list[tuple[str, float]]]) -> dict | None:
    volatility = {name: _rolling_volatility(rows) for name, rows in raw_series.items()}
    insight = "Volatilidad realizada a 20 sesiones; mide variación pasada, no dirección ni probabilidad futura."
    return _line_payload(
        volatility, title="Volatilidad realizada del petróleo",
        subtitle="Ventana móvil de 20 sesiones · anualizada · porcentaje",
        colors={"WTI": "#FF9D00", "Brent": "#E8E8E8"}, insight=insight,
        decimals=1, prefix="", suffix="%",
    )


def generate_openbb_market_chart(destination: Path) -> Path | None:
    chart, _, _ = generate_openbb_market_chart_result(destination)
    return chart


def generate_openbb_market_chart_result(destination: Path) -> tuple[Path | None, str | None, str | None]:
    raw_series: dict[str, list[tuple[str, float]]] = {}
    symbols_by_name = {
        "WTI": ("CL", "CL=F"),
        "Brent": ("BZ", "BZ=F"),
        "RBOB": ("RB", "RB=F"),
        "ULSD": ("HO", "HO=F"),
    }
    for name, symbols in symbols_by_name.items():
        for symbol in symbols:
            try:
                rows = _history(symbol)
                if _plausible_market_series(name, rows):
                    raw_series[name] = rows
                    break
            except Exception:
                continue
    payload = build_market_payload({key: value for key, value in raw_series.items() if key in {"WTI", "Brent"}})
    if not payload:
        return None, None, None
    briefing = build_market_briefing(raw_series)
    stable_payload = {
        "labels": payload["labels"],
        "datasets": payload["datasets"],
        "references": payload["references"],
        "insight": payload["insight"],
        "briefing": briefing,
    }
    signature = hashlib.sha256(
        json.dumps(stable_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return render_chart(payload, destination), signature, briefing


def generate_openbb_market_bundle(
    destination_dir: Path,
    related_news: list[dict] | None = None,
    warehouse_path: Path | None = None,
) -> tuple[list[tuple[str, Path, str]], str | None]:
    """Create a compact close package: crude, refined products and volatility."""
    raw_series: dict[str, list[tuple[str, float]]] = {}
    symbols_by_name = {
        "WTI": ("CL", "CL=F"), "Brent": ("BZ", "BZ=F"),
        "RBOB": ("RB", "RB=F"), "ULSD": ("HO", "HO=F"),
    }
    for name, symbols in symbols_by_name.items():
        for symbol in symbols:
            try:
                rows = _history(symbol)
                if _plausible_market_series(name, rows):
                    raw_series[name] = rows
                    break
            except Exception:
                continue
    if warehouse_path is not None and raw_series:
        try:
            from .warehouse import save_market_history

            save_market_history(warehouse_path, raw_series)
        except Exception:
            # Arch at the archive layer must never stop Telegram delivery.
            pass
    specs = [
        ("crude", build_market_payload({k: v for k, v in raw_series.items() if k in {"WTI", "Brent"}}), "Petróleo: cierre diario"),
        ("products", build_refined_products_payload({k: v for k, v in raw_series.items() if k in {"RBOB", "ULSD"}}), "Gasolina y diésel: cierre diario"),
        ("volatility", build_volatility_payload({k: v for k, v in raw_series.items() if k in {"WTI", "Brent"}}), "Volatilidad del petróleo"),
    ]
    artifacts: list[tuple[str, Path, str]] = []
    destination_dir.mkdir(parents=True, exist_ok=True)
    for key, payload, caption in specs:
        if not payload:
            continue
        stable = {name: payload[name] for name in ("labels", "datasets", "references", "insight")}
        signature = hashlib.sha256(json.dumps(stable, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        artifacts.append((key, render_chart(payload, destination_dir / f"market-{key}.png"), signature))
    heatmap = build_market_heatmap_payload(raw_series)
    if heatmap:
        signature = hashlib.sha256(json.dumps(heatmap["rows"], sort_keys=True).encode("utf-8")).hexdigest()
        artifacts.append(("heatmap", render_heatmap(heatmap, destination_dir / "market-heatmap.png"), signature))
    return artifacts, build_market_briefing(raw_series, related_news) if raw_series else None
