"""Yahoo Finance v8 (chart API) as the fast market lane: keyless CME closes.

Primary source for WTI (CL=F), Brent (BZ=F), RBOB (RB=F) and ULSD (HO=F)
daily closes. The ladder in ``market_intelligence.load_market_series`` remains
EIA API → eia.gov/dnav → snapshot behind it, so an Yahoo outage degrades to the
official lane without ever inventing a number.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from math import isfinite
from urllib.parse import urlencode

FUTURES = {
    "WTI": "CL=F",
    "Brent": "BZ=F",
    "RBOB": "RB=F",
    "ULSD": "HO=F",
}
# A single-session move beyond this size in these contracts means broken data,
# not a market event; the point is dropped and the series keeps moving.
MAX_DAILY_MOVE = 0.25
_API_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FuelIntelligenceEcuador/0.4"}
_PROVIDER = "Yahoo Finance (futuros CME)"


def _rows_from_payload(payload: dict) -> list[tuple[str, float]]:
    result = payload.get("chart", {}).get("result") or []
    if not result:
        return []
    stamps = result[0].get("timestamp") or []
    quote = (result[0].get("indicators", {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    adjusted = ((result[0].get("indicators", {}).get("adjclose") or [{}])[0]).get("adjclose") or closes
    rows: dict[str, float] = {}
    for stamp, close, close_adjusted in zip(stamps, closes, adjusted):
        if not stamp:
            continue
        value = close_adjusted if close_adjusted is not None else close
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if not isfinite(number) or number <= 0:
            continue
        day = datetime.fromtimestamp(stamp, tz=timezone.utc).date().isoformat()
        rows[day] = number
    # One-session moves beyond MAX_DAILY_MOVE are glitches, not markets.
    cleaned: list[tuple[str, float]] = []
    previous: float | None = None
    for day in sorted(rows):
        value = rows[day]
        if previous and previous > 0 and abs(value / previous - 1) > MAX_DAILY_MOVE:
            continue
        cleaned.append((day, value))
        previous = value
    return cleaned


def _fetch_host(host: str, symbol: str, range_label: str) -> dict | None:
    import urllib.request

    url = (
        f"https://{host}/v8/finance/chart/{symbol}?"
        + urlencode({"interval": "1d", "range": range_label, "includePrePost": "false"})
    )
    request = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def fetch_daily_close(symbol: str, *, range_label: str = "6mo") -> list[tuple[str, float]]:
    """Observed daily closes for one contract, oldest first."""
    for host in _API_HOSTS:
        payload = _fetch_host(host, symbol, range_label)
        if payload is None:
            continue
        rows = _rows_from_payload(payload)
        if rows:
            return rows
    return []


def build_market_rows(
    names: tuple[str, ...] = tuple(FUTURES), *, range_label: str = "6mo"
) -> dict[str, list[tuple[str, float]]]:
    """Fetch every requested series; a failed symbol is simply absent."""
    output: dict[str, list[tuple[str, float]]] = {}
    for name in names:
        symbol = FUTURES.get(name)
        if not symbol:
            continue
        rows = fetch_daily_close(symbol, range_label=range_label)
        if len(rows) >= 2:
            output[name] = rows
    return output


if __name__ == "__main__":
    import sys

    names = tuple(sys.argv[1:]) or tuple(FUTURES)
    series = build_market_rows(names)
    for name, rows in series.items():
        last_day, last_value = rows[-1]
        print(f"{name}: {last_value:.2f} @ {last_day} ({len(rows)} cierres)")
    if not series:
        print("sin datos de Yahoo")
