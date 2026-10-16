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


def fetch_ecuador_crude_production(
    api_key: str, timeout_seconds: int = 20, length: int = 60
) -> list[tuple[str, float]]:
    """Monthly Ecuador crude oil production (thousand barrels per day), EIA API v2.

    Only the verified international route is used: activity=Production,
    product=Crude oil including lease condensate, country=Ecuador. A missing key
    or an implausible series returns nothing rather than a guess.
    """
    if not api_key.strip():
        return []
    query: list[tuple[str, str]] = [
        ("api_key", api_key.strip()),
        ("frequency", "monthly"),
        ("data[0]", "value"),
        ("facets[activityId][]", "1"),
        ("facets[productId][]", "57"),
        ("facets[countryRegionId][]", "ECU"),
        ("sort[0][column]", "period"),
        ("sort[0][direction]", "desc"),
        ("length", str(length)),
    ]
    url = "https://api.eia.gov/v2/international/data/?" + urllib.parse.urlencode(query)
    request = urllib.request.Request(url, headers={"User-Agent": "FuelIntelligenceEcuador/0.4"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    rows: list[tuple[str, float]] = []
    for row in payload.get("response", {}).get("data", []):
        period = str(row.get("period", ""))[:7]
        try:
            value = float(row.get("value"))
        except (TypeError, ValueError):
            continue
        # Ecuador produces roughly 400-500 thousand barrels per day. A value far
        # outside a broad sanity band means a wrong series, so it is refused.
        if len(period) == 7 and period[4] == "-" and 1.0 <= value <= 5000.0:
            rows.append((period, value))
    return sorted(set(rows))
