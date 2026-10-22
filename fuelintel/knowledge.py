"""Domain facts the bot must know before it plots any series.

Every metric the bot draws carries three things here: its unit, what it
actually means, and the band of values that is historically reasonable.  The
quality gate uses the band to refuse numbers that cannot be trusted, and the
writers use the meaning to describe each chart without inventing anything.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MetricFact:
    """What a series is, its unit, and the range that is physically sane."""

    key: str
    unit: str
    meaning: str
    low: float
    high: float


FACTS: dict[str, MetricFact] = {
    "WTI": MetricFact("WTI", "USD/barril", "referencia de crudo estadounidense", 20.0, 250.0),
    "Brent": MetricFact("Brent", "USD/barril", "referencia de crudo europea", 20.0, 250.0),
    "RBOB": MetricFact("RBOB", "USD/galón", "gasolina de mezcla estadounidense", 0.5, 10.0),
    "ULSD": MetricFact("ULSD", "USD/galón", "diésel de ultra bajo azufre", 0.5, 10.0),
    # Refining margins (crack spreads) live well inside this band in normal
    # markets, even in the 2022 squeeze.  Anything far outside is far more
    # likely a bad quote than a real signal, so it is quarantined.
    "crack_gasolina": MetricFact(
        "crack_gasolina", "USD/barril", "margen de refino de gasolina RBOB contra WTI", -20.0, 70.0
    ),
    "crack_diesel": MetricFact(
        "crack_diesel", "USD/barril", "margen de refino de diésel ULSD contra WTI", -20.0, 70.0
    ),
}


@dataclass(frozen=True, slots=True)
class Concept:
    """A financial idea the bot must understand before it can use it."""

    key: str
    definition: str
    formula: str
    applied_as: str


CONCEPTS: dict[str, Concept] = {
    "retorno": Concept(
        "retorno",
        "cambio relativo del precio entre dos observaciones",
        "r = Pₜ / Pₜ₋₁ − 1",
        "variación diaria y semanal de cada serie",
    ),
    "volatilidad": Concept(
        "volatilidad",
        "dispersión de los retornos; mide incertidumbre, no dirección",
        "σ anual = desv(retornos) × √252",
        "lectura cuantitativa de WTI y Brent, con su percentil histórico",
    ),
    "correlacion": Concept(
        "correlación",
        "grado en que dos series se mueven juntas; no implica causalidad",
        "ρ = cov(a,b) / (σa · σb) sobre retornos alineados",
        "co-movimiento de RBOB y ULSD con WTI",
    ),
    "beta": Concept(
        "beta",
        "sensibilidad de los retornos de un activo frente a su referencia",
        "β = cov(a,b) / var(b)",
        "cuánto responde una serie a los movimientos del crudo",
    ),
    "percentil": Concept(
        "percentil",
        "posición de un valor dentro de la historia observada, 0–100",
        "rango = (valores ≤ x) / total × 100",
        "dónde está el último cierre dentro de su propio histórico",
    ),
    "drawdown": Concept(
        "drawdown",
        "mayor caída desde un máximo previo hasta un mínimo posterior",
        "máx( Pₜ / máx(P₀..Pₜ) − 1 )",
        "profundidad de la mayor caída del periodo observado",
    ),
    "zscore": Concept(
        "z-score robusto",
        "cuántas desviaciones típicas robustas se aleja el dato de su centro",
        "z = 0.6745 × (x − mediana) / MAD",
        "alertas de movimiento o régimen atípico",
    ),
    "crack_spread": Concept(
        "crack spread",
        "margen teórico de refino: producto destilado menos crudo",
        "RBOB × 42 − WTI; ULSD × 42 − WTI",
        "proxy del costo de importar refinados, referencia para Ecuador",
    ),
    "brecha": Concept(
        "brecha de referencia",
        "diferencia medida entre el precio regulado y la cotización internacional",
        "brecha = precio_oficial − referencia (misma unidad)",
        "comparación honesta USD/gal; no incluye impuestos ni subsidios",
    ),
    "contango": Concept(
        "contango / backwardation",
        "estructura temporal: futuros lejanos por encima (contango) o por debajo (backwardation) del cercano",
        "pendiente = F_lejano − F_cercano",
        "solo descriptible con varios vencimientos; el bot no lo inventa",
    ),
}


def concept(key: str) -> Concept | None:
    return CONCEPTS.get(key)


def fact(key: str) -> MetricFact | None:
    return FACTS.get(key)


# Stable Ecuador fuel-market rules. They are context for interpretation, never
# quotations: no review may announce an Ecuador price change triggered by crude.
ECUADOR_RULES: tuple[str, ...] = (
    "Las vigencias de precios de combustibles en Ecuador son mensuales: del 12 al 11 del mes siguiente; cada ajuste lo anuncia una resolución oficial.",
    "Los precios al consumidor están regulados con bandas y subsidios: un movimiento del crudo no implica un cambio en la bomba.",
    "Extra y Ecopaís amparan un subsidio parcial estatal; el combustible Súper se publica como precio sugerido sin subsidio, y el diésel mantiene subsidio en varios sectores.",
    "Un titular sobre caída o subida del crudo NO es un cambio de precios en Ecuador ni puede redactarse como tal.",
    "Las cifras ofician con resolución y vigencia: una cifra de prensa o de un funcionario es atribución, no ficha oficial.",
    "El crudo y los refinos explican presión sobre exportaciones e importación del país, no la tarifa final regulada.",
)


def ecuador_rules_text() -> str:
    return "\n".join(f"- {line}" for line in ECUADOR_RULES)


def within_band(key: str, value: float) -> bool:
    """A missing metric is never blocked; an unknown series is trusted as-is."""
    spec = FACTS.get(key)
    if spec is None:
        return True
    return spec.low <= value <= spec.high


def band_text(key: str) -> str:
    spec = FACTS.get(key)
    if spec is None:
        return ""
    return f"{spec.low:g}–{spec.high:g} {spec.unit}"
