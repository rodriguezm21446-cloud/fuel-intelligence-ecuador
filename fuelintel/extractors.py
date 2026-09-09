from __future__ import annotations

import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from .calculations import validate_price
from .models import EffectivePeriod, FuelPrice
from .text import collapse_spaces, fold


MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

PRODUCT_ALIASES = {
    "extra": "Extra",
    "ecopais": "Ecopaís",
    "gasolina extra": "Extra",
    "gasolina ecopais": "Ecopaís",
    "super": "Súper",
    "super premium": "Súper",
    "gasolina super": "Súper",
    "diesel": "Diésel Premium",
    "diesel premium": "Diésel Premium",
}


def _safe_date(year: int, month_name: str, day: int) -> date | None:
    try:
        return date(year, MONTHS[month_name], day)
    except (KeyError, ValueError):
        return None


def extract_effective_period(text: str, reference: date | None = None) -> EffectivePeriod | None:
    reference = reference or date.today()
    value = fold(text)
    pattern = re.compile(
        r"(?P<d1>\d{1,2})\s+de\s+(?P<m1>enero|febrero|marzo|abril|mayo|junio|julio|agosto|setiembre|septiembre|octubre|noviembre|diciembre)"
        r"(?:\s+(?:de\s+)?(?P<y1>20\d{2}))?\s*(?:al|a|hasta|[-–—])\s*"
        r"(?P<d2>\d{1,2})\s+de\s+(?P<m2>enero|febrero|marzo|abril|mayo|junio|julio|agosto|setiembre|septiembre|octubre|noviembre|diciembre)"
        r"(?:\s+(?:de\s+)?(?P<y2>20\d{2}))?"
    )
    match = pattern.search(value)
    if not match:
        return None

    end_year = int(match.group("y2") or match.group("y1") or reference.year)
    start_year = int(match.group("y1") or end_year)
    if not match.group("y1") and MONTHS[match.group("m1")] > MONTHS[match.group("m2")]:
        start_year -= 1

    start = _safe_date(start_year, match.group("m1"), int(match.group("d1")))
    end = _safe_date(end_year, match.group("m2"), int(match.group("d2")))
    if not start or not end or end < start or (end - start).days > 400:
        return None
    return EffectivePeriod(start, end)


def _decimal(raw: str) -> Decimal | None:
    cleaned = raw.strip().replace(" ", "")
    if "," in cleaned and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def extract_fuel_prices(
    text: str,
    source_url: str,
    observed_at: datetime,
    reference: date | None = None,
) -> list[FuelPrice]:
    compact = collapse_spaces(text)
    normalized = fold(compact)
    period = extract_effective_period(compact, reference)
    found: dict[str, Decimal] = {}

    shared = re.finditer(
        r"(?:gasolina\s+)?(extra)\s*/\s*(ecopais)\s*(?:[:=]|usd|\$)?\s*\$?\s*(\d{1,2}(?:[.,]\d{1,4})?)",
        normalized,
    )
    for match in shared:
        value = _decimal(match.group(3))
        if value is not None and validate_price(value)[0]:
            found["Extra"] = value
            found["Ecopaís"] = value

    aliases = "|".join(sorted((re.escape(item) for item in PRODUCT_ALIASES), key=len, reverse=True))
    pattern = re.compile(
        rf"\b(?P<product>{aliases})\b(?:\s+(?:referencial|automotriz))?\s*(?:[:=]|usd|\$)?\s*\$?\s*(?P<price>\d{{1,2}}(?:[.,]\d{{1,4}})?)"
    )
    for match in pattern.finditer(normalized):
        value = _decimal(match.group("price"))
        product = PRODUCT_ALIASES[match.group("product")]
        if value is not None and validate_price(value)[0]:
            found[product] = value

    return [
        FuelPrice(
            product=product,
            price=value,
            period=period,
            source_url=source_url,
            observed_at=observed_at,
        )
        for product, value in sorted(found.items())
    ]


def extract_pdf_text(payload: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("Instala pypdf para procesar documentos PDF") from exc
    reader = PdfReader(io.BytesIO(payload), strict=False)
    pages: list[str] = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n".join(pages)

