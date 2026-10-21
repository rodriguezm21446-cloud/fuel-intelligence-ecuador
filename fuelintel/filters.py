from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .models import Candidate, Decision, EffectivePeriod, VerificationStatus
from .text import fold


HIGH_VALUE_TERMS = {
    "gasolina": 5,
    "ecopais": 7,
    "extra": 3,
    "super": 3,
    "diesel": 5,
    "combustible": 5,
    "petroleo": 4,
    "crudo": 4,
    "brent": 6,
    "wti": 6,
    "opep": 5,
    "refineria": 4,
    "subsidio": 5,
    "hidrocarburo": 5,
    "escasez": 5,
    "abastecimiento": 5,
    "desabastecimiento": 6,
    "gasolinera": 4,
    "expendedor": 3,
    "octanaje": 3,
    "petrolero": 4,
    "oleoducto": 5,
    "importacion de combustible": 6,
    "precio": 2,
    "importacion": 2,
    "derivados": 3,
    "petroecuador": 5,
    "arch": 4,
    "gasoline": 5,
    "diesel fuel": 5,
    "crude oil": 4,
    "refinery": 4,
    "refining": 3,
    "inventory": 4,
    "inventories": 4,
    "shortage": 6,
    "outage": 5,
    "volatility": 3,
    "rbob": 6,
    "ulsd": 6,
    "pipeline": 5,
    "tanker": 5,
    "hormuz": 7,
    "strait of hormuz": 8,
    "oil supply": 6,
    "production cut": 6,
    "export ban": 7,
    "sanction": 4,
    "drone strike": 6,
    "oil terminal": 6,
    "port closure": 7,
    "houthi": 6,
    "oil": 4,
    "attack": 2,
    "vessel": 4,
    "shipping": 4,
    "maritime": 3,
    "bab al-mandab": 7,
    "نفط": 6,
    "الخام": 5,
    "بنزين": 6,
    "ديزل": 6,
    "مصفاة": 5,
    "هرمز": 8,
    "ناقلة": 5,
    "أوبك": 6,
    "إمدادات النفط": 7,
    "هجوم": 2,
    "sancion": 4,
    "recorte de produccion": 6,
    "prohibicion de exportacion": 7,
    "ataque con drones": 6,
    "terminal petrolera": 6,
    "cierre de puerto": 7,
    "huti": 6,
}

EXCLUSION_TERMS = {
    "futbol", "farandula", "receta", "horoscopo", "loteria", "entretenimiento",
}


AMBIGUOUS_TERMS = {"extra", "super", "arch", "oil"}


# Primary fuel-domain vocabulary for the HEADLINE. The operator's channel only
# carries stories whose front page already talks fuel; a story whose grounding
# only appears in the body smells like incidental mention and stays as context.
PRIMARY_FUEL_TERMS = {
    "gasolina", "gasolinas", "diesel", "ecopais", "crudo", "petroleo",
    "refineria", "derivados", "combustible", "combustibles", "hidrocarburo",
    "oleoducto", "brent", "wti", "opep", "hormuz", "ormuz", "gasolinera",
    "banda de precios", "precio de la gasolina", "precios de la gasolina",
    "precio de los combustibles", "precios de los combustibles",
    "gas price", "gas prices", "gasoline", "crude oil", "fuel price", "fuel prices",
    "oil price", "oil prices", "diesel fuel", "refining", "crude",
}

def has_primary_fuel_title(candidate: Candidate) -> bool:
    """True when the headline itself belongs to the fuel domain."""
    title = fold(candidate.title)
    if any(term in title for term in PRIMARY_FUEL_TERMS):
        return True
    # Official national regulators may publish generic document titles while
    # the payload carries the price structure; they never fail the title gate.
    return (
        candidate.source.country == "EC"
        and candidate.source.trust >= 90
        and any(
            word in fold(f"{candidate.title} {candidate.summary} {candidate.body}")
            for word in ("precio", "banda", "vigencia", "galon", "pvp", "derivados")
        )
    )

# Ecuador-focused energy words. A local fuel or energy story matters to the
# operator even before an official price is published, so the Ecuadorian
# sources receive a small, explicit boost for them.
ECUADOR_ENERGY_TERMS = {
    "gasolina", "ecopais", "extra", "super", "diesel", "combustible",
    "hidrocarburo", "petroleo", "crudo", "gasolinera", "refineria",
    "subsidio", "petroecuador", "arch", "escasez", "abastecimiento",
    "oleoducto", "petrolero",
}

# Fast local lane ("squawk de aquí"): a fuel mention plus either an actor
# (president/minister) or an explicit change word. The president often speaks
# before the official PDF and the media echo it first, so an actor word alone is
# enough. This mirrors isDomesticPriceSignal in the Cloudflare worker.
ECUADOR_FUEL_TERMS = {
    "gasolina", "ecopais", "extra", "super", "diesel", "combustible",
    "hidrocarburo", "petroleo", "crudo", "gasolinera", "refineria",
    "subsidio", "derivados",
}

ECUADOR_ACTOR_TERMS = {
    "noboa", "presidente", "ministro", "ministerio de energia", "gobierno", "ejecutivo",
}

ECUADOR_CHANGE_TERMS = {
    "precio", "sube", "baja", "bajara", "subira", "aumento", "incremento", "reduccion",
    "alza", "rebaja", "decreto", "resolucion", "centavos", "nuevo precio", "desde el 12",
    "rigen", "escasez", "abastecimiento", "desabastecimiento",
}


# Ecuador anchors: words that tie a story to Ecuador even when an international
# outlet reports it. A foreign market story without an anchor is kept as context
# for the analyst but is never published, which keeps the channel free of noise.
ECUADOR_ANCHOR_TERMS = {
    "ecuador", "ecuatoriano", "ecuatoriana", "petroecuador", "ep petroecuador",
    "arch", "esmeraldas", "shushufindi", "lago agrio", "sacha", "ocp", "sote",
    "refineria de esmeraldas", "ecopais", "noboa", "banda de precios",
}


def has_ecuador_anchor(candidate: Candidate) -> bool:
    """True when a story explicitly names Ecuador or one of its fuel assets."""
    text = fold(f"{candidate.title} {candidate.summary} {candidate.body}")
    return any(term in text for term in ECUADOR_ANCHOR_TERMS)


# Major international supply events that can move Ecuador's import/export costs
# even without naming the country. They are the only foreign stories that are
# published (translated and summarised); the rest stays context-only.
HIGH_IMPACT_FOREIGN_TERMS = {
    "hormuz", "strait of hormuz", "ormuz", "bab al-mandab", "red sea", "mar rojo",
    "suez", "houthi", "huti", "opec", "opep", "production cut", "recorte de produccion",
    "output cut", "export ban", "prohibicion de exportacion", "sanction", "sancion",
    "embargo", "pipeline outage", "oleoducto", "refinery outage", "refinery fire",
    "refinery explosion", "incendio en la refineria", "tanker seized", "buque secuestrado",
    "drone strike", "ataque con drones", "oil supply", "suministro de petroleo",
    "supply disruption", "interrupcion del suministro", "port closure", "cierre de puerto",
    "supertanker", "oil terminal", "terminal petrolera", "strategic reserve",
    "reserva estrategica", "spare capacity",
    "هرمز", "باب المندب", "أوبك", "هجوم", "عقوبات", "إمدادات", "مصفاة", "ناقلة",
}


def is_high_impact_foreign(candidate: Candidate) -> bool:
    """True when a foreign story is a major supply event worth publishing."""
    text = fold(f"{candidate.title} {candidate.summary} {candidate.body}")
    return any(term in text for term in HIGH_IMPACT_FOREIGN_TERMS)


def _contains(text: str, term: str) -> bool:
    if term not in AMBIGUOUS_TERMS:
        return term in text
    return re.search(rf"(?<![\w]){re.escape(term)}(?![\w])", text, flags=re.UNICODE) is not None


def is_fast_fuel_signal(candidate: Candidate) -> bool:
    """True when a story matches the local fast lane: fuel AND (actor OR change)."""
    text = fold(f"{candidate.title} {candidate.summary} {candidate.body}")
    if not any(_contains(text, term) for term in ECUADOR_FUEL_TERMS):
        return False
    return any(term in text for term in ECUADOR_ACTOR_TERMS) or any(
        term in text for term in ECUADOR_CHANGE_TERMS
    )


def relevance_score(candidate: Candidate) -> int:
    title = fold(candidate.title)
    body = fold(f"{candidate.summary} {candidate.body}")
    full = f"{title} {body}"
    score = 0
    for term, weight in HIGH_VALUE_TERMS.items():
        if _contains(title, term):
            score += weight * 2
        elif _contains(body, term):
            score += weight
    if candidate.source.country == "EC":
        if "ecuador" in full:
            score += 3
        if any(_contains(full, term) for term in ECUADOR_ENERGY_TERMS):
            score += 3
        if is_fast_fuel_signal(candidate):
            score += 4
    score -= sum(10 for term in EXCLUSION_TERMS if term in title)
    return score


def evaluate_candidate(
    candidate: Candidate,
    now: datetime,
    effective_period: EffectivePeriod | None = None,
    *,
    ecuador_only: bool = False,
) -> Decision:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    score = relevance_score(candidate)
    reasons: list[str] = []

    if score < 5:
        return Decision(VerificationStatus.REJECTED, 0, ("contenido no relacionado",), score)

    # Headline gate: only fuel-domain headlines (or major supply events) reach
    # the operator. Everything else stays in storage as context and never pages.
    if not has_primary_fuel_title(candidate) and not is_high_impact_foreign(candidate):
        return Decision(
            VerificationStatus.REJECTED,
            0,
            ("el titular no es del dominio de combustibles: se conserva solo como contexto",),
            score,
        )

    if (
        ecuador_only
        and candidate.source.country != "EC"
        and not has_ecuador_anchor(candidate)
        and not is_high_impact_foreign(candidate)
    ):
        return Decision(
            VerificationStatus.REJECTED,
            0,
            ("noticia internacional común sin anclaje Ecuador: se conserva solo como contexto",),
            score,
        )

    if effective_period is not None:
        local_day = now.date()
        if effective_period.end < local_day:
            return Decision(VerificationStatus.STALE, 0, ("periodo de vigencia vencido",), score)
        if effective_period.start > local_day + timedelta(days=45):
            return Decision(VerificationStatus.REJECTED, 0, ("periodo futuro improbable",), score)
        reasons.append("periodo de vigencia comprobado")

    if candidate.published_at is None:
        if effective_period is None:
            return Decision(
                VerificationStatus.PENDING,
                min(candidate.source.trust, 60),
                ("la fuente no expone una fecha verificable",),
                score,
            )
    else:
        published = candidate.published_at
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        age = now.astimezone(timezone.utc) - published.astimezone(timezone.utc)
        if age < timedelta(minutes=-10):
            return Decision(VerificationStatus.REJECTED, 0, ("fecha futura incoherente",), score)
        if age > timedelta(hours=candidate.source.max_age_hours) and effective_period is None:
            return Decision(VerificationStatus.STALE, 0, ("publicación fuera de la ventana de actualidad",), score)
        reasons.append("fecha de publicación vigente")

    if candidate.source.trust >= 90:
        reasons.append("fuente primaria u oficial")
        return Decision(VerificationStatus.VERIFIED, min(100, candidate.source.trust), tuple(reasons), score)

    reasons.append("requiere confirmación independiente")
    return Decision(VerificationStatus.PENDING, min(79, candidate.source.trust), tuple(reasons), score)
