from __future__ import annotations

from datetime import date, timedelta
import json
import re
import urllib.request


BCE_BALANCE_URL = (
    "https://contenido.bce.fin.ec/documentos/Estadisticas/SectorExterno/"
    "BalanzaPagos/balanzaComercial/ebc{code}.html"
)

_MONTHS = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12,
}
_WIDGET = re.compile(r'data-for="htmlwidget-[^"]+">\s*(\{.*?\})\s*</script>', re.S)
_MONTH_LABEL = re.compile(r"^([a-zA-ZÁÉÍÓÚáéíóú]{3})[-/\s]?(\d{2})$")
_SERIAL = re.compile(r"^\d{5}$")
_EXCEL_ORIGIN = date(1899, 12, 30)


def _publication_codes(today: date, months_back: int) -> list[str]:
    """Candidate monthly publication codes, newest first.

    The BCE "Balanza Comercial" report lags the reference month, so the newest
    possible publication is probed first and older months are tried as fallback.
    """
    codes: list[str] = []
    year, month = today.year, today.month
    for _ in range(months_back):
        codes.append(f"{year:04d}{month:02d}")
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    return codes


def _category_month(label: object) -> str | None:
    """Translate a BCE chart category into a YYYY-MM period.

    Categories are usually short month names ("nov-24"). Some widgets leak a
    spreadsheet serial number instead of a label; that is decoded safely. Any
    unrecognized label is refused so a value is never placed on a wrong month.
    """
    text = str(label).strip()
    match = _MONTH_LABEL.match(text)
    if match:
        month = _MONTHS.get(match.group(1).casefold())
        if month:
            return f"20{match.group(2)}-{month:02d}"
        return None
    if _SERIAL.match(text):
        serial = int(text)
        if 40000 <= serial <= 60000:
            parsed = _EXCEL_ORIGIN + timedelta(days=serial)
            return f"{parsed.year:04d}-{parsed.month:02d}"
    return None


def _text(value: object) -> str:
    """Flatten a Highcharts text field, which may be a string, dict or list."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return _text(value.get("text", ""))
    if isinstance(value, list):
        return " ".join(_text(item) for item in value)
    return ""


def _axis(options: dict, key: str) -> dict:
    """Highcharts axes are either an object or a list of objects."""
    value = options.get(key)
    if isinstance(value, list):
        return value[0] if value and isinstance(value[0], dict) else {}
    return value if isinstance(value, dict) else {}


def _crude_exports_options(html: str) -> dict | None:
    """Return the Highcharts options of the crude/derivatives export figure."""
    for match in _WIDGET.finditer(html):
        try:
            widget = json.loads(match.group(1))
        except (ValueError, TypeError):
            continue
        options = widget.get("x", {}).get("hc_opts")
        if not isinstance(options, dict):
            continue
        series = options.get("series")
        if not isinstance(series, list):
            continue
        names = {_text(item.get("name")).casefold() for item in series if isinstance(item, dict)}
        if "crudo" not in names or "derivados" not in names:
            continue
        title = _text(options.get("title")).casefold()
        axis = _text(_axis(options, "yAxis").get("title")).casefold()
        if "petr" in title and "millones usd" in axis:
            return options
    return None


def _crude_rows(options: dict) -> list[tuple[str, float]]:
    categories = _axis(options, "xAxis").get("categories") or []
    crude = next(
        (
            item
            for item in options.get("series", [])
            if isinstance(item, dict) and _text(item.get("name")).casefold() == "crudo"
        ),
        None,
    )
    if crude is None:
        return []
    values = crude.get("data") or []
    rows: dict[str, float] = {}
    for label, raw in zip(categories, values):
        period = _category_month(label)
        if period is None:
            continue
        scalar = raw[-1] if isinstance(raw, (list, tuple)) and raw else raw
        try:
            value = float(scalar)
        except (TypeError, ValueError):
            continue
        # Ecuador exports roughly USD 300-900 million of crude per month. A wide
        # band rejects wrong units or wrong series instead of charting them.
        if 10.0 <= value <= 20000.0:
            rows[period] = value
    return sorted(rows.items())


def fetch_ecuador_crude_exports(
    today: date | None = None, timeout_seconds: int = 30, months_back: int = 6
) -> list[tuple[str, float]]:
    """Monthly Ecuador crude oil export value (USD millions, FOB) from the BCE.

    Reads the official "Balanza Comercial" publication. Only a figure that is
    explicitly labelled in USD millions of crude is accepted; anything missing
    or ambiguous returns nothing, so an unverified number is never charted.
    """
    reference = today or date.today()
    best: list[tuple[str, float]] = []
    for code in _publication_codes(reference, months_back):
        url = BCE_BALANCE_URL.format(code=code)
        request = urllib.request.Request(url, headers={"User-Agent": "FuelIntelligenceEcuador/0.4"})
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read()
        except Exception:
            continue
        try:
            html = raw.decode("utf-8")
        except UnicodeDecodeError:
            html = raw.decode("latin-1", errors="replace")
        options = _crude_exports_options(html)
        if options is None:
            continue
        rows = _crude_rows(options)
        if len(rows) >= 6:
            return rows
        if len(rows) > len(best):
            best = rows
    return best if len(best) >= 2 else []
