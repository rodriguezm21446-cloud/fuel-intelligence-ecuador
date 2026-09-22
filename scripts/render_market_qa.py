from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from fuelintel.chart_renderer import render_chart, render_heatmap
from fuelintel.market_intelligence import (
    build_market_heatmap_payload,
    build_market_payload,
    build_refined_products_payload,
    build_volatility_payload,
    build_weekly_moves_payload,
)


def fixture() -> dict[str, list[tuple[str, float]]]:
    start = date(2026, 5, 1)
    rows: dict[str, list[tuple[str, float]]] = {name: [] for name in ("WTI", "Brent", "RBOB", "ULSD")}
    for index in range(120):
        day = start + timedelta(days=index)
        if day.weekday() >= 5:
            continue
        wave = ((index % 17) - 8) * 0.22
        rows["WTI"].append((day.isoformat(), 72 + index * 0.05 + wave))
        rows["Brent"].append((day.isoformat(), 76 + index * 0.055 + wave * 0.8))
        rows["RBOB"].append((day.isoformat(), 2.25 + index * 0.0015 + wave * 0.012))
        rows["ULSD"].append((day.isoformat(), 2.48 + index * 0.0018 + wave * 0.014))
    return rows


def main() -> int:
    output = Path("data/charts/market-qa-v2")
    output.mkdir(parents=True, exist_ok=True)
    rows = fixture()
    payloads = {
        "crude": build_market_payload({key: rows[key] for key in ("WTI", "Brent")}),
        "products": build_refined_products_payload({key: rows[key] for key in ("RBOB", "ULSD")}),
        "volatility": build_volatility_payload({key: rows[key] for key in ("WTI", "Brent")}),
        "weekly-moves": build_weekly_moves_payload(rows),
    }
    for name, payload in payloads.items():
        if payload:
            render_chart(payload, output / f"{name}.png")
    heatmap = build_market_heatmap_payload(rows)
    if heatmap:
        render_heatmap(heatmap, output / "heatmap.png")
    print(output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
