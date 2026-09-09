from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from .models import PriceChange


CENT = Decimal("0.01")
THOUSANDTH = Decimal("0.001")


def money(value: Decimal, places: Decimal = THOUSANDTH) -> Decimal:
    return value.quantize(places, rounding=ROUND_HALF_UP)


def price_change(current: Decimal, previous: Decimal | None) -> PriceChange:
    current = money(current)
    if previous is None:
        return PriceChange(current, None, None, None)
    previous = money(previous)
    absolute = money(current - previous)
    percent = None if previous == 0 else ((current - previous) / previous * 100).quantize(CENT, rounding=ROUND_HALF_UP)
    return PriceChange(current, previous, absolute, percent)


def validate_price(value: Decimal) -> tuple[bool, str]:
    if value <= 0:
        return False, "el precio no puede ser cero o negativo"
    if value < Decimal("0.20") or value > Decimal("10.00"):
        return False, "el precio por galón está fuera del rango de seguridad"
    return True, "precio dentro del rango de seguridad"


def direction(change: PriceChange) -> str:
    if change.absolute is None:
        return "sin comparación histórica"
    if change.absolute > 0:
        return "sube"
    if change.absolute < 0:
        return "baja"
    return "sin cambios"

