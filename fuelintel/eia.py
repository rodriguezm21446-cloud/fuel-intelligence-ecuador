from __future__ import annotations

import json
import urllib.parse
import urllib.request


EIA_RETAIL_SERIES = {
    "Gasolina regular": "EMM_EPMR_PTE_NUS_DPG",
    "Gasolina premium": "EMM_EPMP_PTE_NUS_DPG",
    "Diésel": "EMD_EPD2D_PTE_NUS_DPG",
}


def fetch_us_retail_prices(api_key: str, timeout_seconds: int = 20, length: int = 180) -> dict[str, list[tuple[str, float]]]:
    """Fetch official weekly U.S. retail prices from EIA API v2.

    Returns only values with a valid date and plausible dollars-per-gallon range.
    A missing key means the optional official series is disabled, never guessed.
    """
    if not api_key.strip():
        return {}
    query: list[tuple[str, str]] = [
        ("api_key", api_key.strip()),
        ("frequency", "weekly"),
        ("data[0]", "value"),
        ("sort[0][column]", "period"),
        ("sort[0][direction]", "desc"),
        ("length", str(length)),
    ]
    for series in EIA_RETAIL_SERIES.values():
        query.append(("facets[series][]", series))
    url = "https://api.eia.gov/v2/petroleum/pri/gnd/data/?" + urllib.parse.urlencode(query)
    request = urllib.request.Request(url, headers={"User-Agent": "FuelIntelligenceEcuador/0.4"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    reverse = {value: key for key, value in EIA_RETAIL_SERIES.items()}
    output: dict[str, dict[str, float]] = {name: {} for name in EIA_RETAIL_SERIES}
    for row in payload.get("response", {}).get("data", []):
        name = reverse.get(str(row.get("series", "")))
        period = str(row.get("period", ""))[:10]
        try:
            value = float(row.get("value"))
        except (TypeError, ValueError):
            continue
        if name and len(period) == 10 and 0.5 <= value <= 15:
            output[name][period] = value
    return {name: sorted(rows.items()) for name, rows in output.items() if rows}
