from __future__ import annotations

import argparse
import tempfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from fuelintel.charts import generate_dashboard
from fuelintel.models import EffectivePeriod, FuelPrice, PriceType
from fuelintel.storage import Store


# Regression fixture copied from official Petroecuador terminal-price PDFs.
# It spans the real transition to Decreto Ejecutivo No. 308 on 28/06/2024.
OFFICIAL_SERIES = (
    (
        date(2024, 6, 12),
        date(2024, 6, 27),
        "Decretos Ejecutivos No. 338, 1158, 724 y 215",
        "https://www.eppetroecuador.ec/wp-content/plugins/download-monitor/download.php?id=3789",
        {"Extra": "2.280641", "Diésel Premium": "1.649297", "Súper": "3.464312"},
    ),
    (
        date(2024, 6, 28),
        date(2024, 7, 11),
        "Decreto Ejecutivo No. 308",
        "https://www.eppetroecuador.ec/wp-content/plugins/download-monitor/download.php?id=3794",
        {"Extra": "2.537204", "Diésel Premium": "1.649297", "Súper": "3.464312"},
    ),
    (
        date(2024, 7, 12),
        date(2024, 8, 11),
        "Decreto Ejecutivo No. 308",
        "https://www.eppetroecuador.ec/wp-content/plugins/download-monitor/download.php?id=3798",
        {"Extra": "2.568169", "Diésel Premium": "1.649297", "Súper": "3.266055"},
    ),
)


def render(destination: Path) -> Path:
    verified = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory() as folder:
        store = Store(Path(folder) / "visual-qa.sqlite3")
        for start, end, decree, source_url, values in OFFICIAL_SERIES:
            for product, value in values.items():
                store.save_price(
                    FuelPrice(
                        product=product,
                        price=Decimal(value),
                        period=EffectivePeriod(start, end),
                        source_url=source_url,
                        observed_at=verified,
                        verified_at=verified,
                        price_type=PriceType.TERMINAL,
                        source_name="Petroecuador",
                        decree_resolution=decree,
                    )
                )
        result = generate_dashboard(store, destination, verified)
        if result is None:
            raise RuntimeError("la prueba visual no produjo un gráfico")
        return result


def render_zoom_crops(chart: Path, output_dir: Path) -> list[Path]:
    """Create lossless 1:1 inspection crops through Chromium, never Pillow."""
    from playwright.sync_api import sync_playwright

    output_dir.mkdir(parents=True, exist_ok=True)
    regions = {
        "top-left": {"x": 0, "y": 0, "width": 1000, "height": 550},
        "top-right": {"x": 2200, "y": 0, "width": 1000, "height": 550},
        "bottom-left": {"x": 0, "y": 1250, "width": 1000, "height": 550},
        "bottom-right": {"x": 2200, "y": 1250, "width": 1000, "height": 550},
        "annotation": {"x": 1150, "y": 300, "width": 900, "height": 1000},
    }
    rendered = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1000, "height": 550})
        page.goto(chart.resolve().as_uri(), wait_until="load")
        page.locator("img").evaluate(
            "element => { element.style.position='fixed'; element.style.width='3200px'; "
            "element.style.height='1800px'; element.style.maxWidth='none'; element.style.margin='0'; }"
        )
        page.locator("body").evaluate(
            "element => { element.style.margin='0'; element.style.overflow='hidden'; }"
        )
        for name, clip in regions.items():
            destination = output_dir / f"{name}.png"
            page.locator("img").evaluate(
                "(element, region) => { element.style.left = (-region.x) + 'px'; "
                "element.style.top = (-region.y) + 'px'; }",
                clip,
            )
            page.screenshot(path=str(destination), full_page=False)
            rendered.append(destination)
        browser.close()
    return rendered


def main() -> int:
    parser = argparse.ArgumentParser(description="Renderiza el fixture oficial para control visual")
    parser.add_argument("output", nargs="?", type=Path, default=Path("data/visual-qa.png"))
    args = parser.parse_args()
    chart = render(args.output)
    crops = render_zoom_crops(chart, chart.parent / "visual-qa-crops")
    print(chart.resolve())
    for crop in crops:
        print(crop.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

