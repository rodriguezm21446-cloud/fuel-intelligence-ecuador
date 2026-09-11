from __future__ import annotations

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
}

EXCLUSION_TERMS = {
    "futbol", "farandula", "receta", "horoscopo", "loteria", "entretenimiento",
}


def relevance_score(candidate: Candidate) -> int:
    title = fold(candidate.title)
    body = fold(f"{candidate.summary} {candidate.body}")
    score = 0
    for term, weight in HIGH_VALUE_TERMS.items():
        if term in title:
            score += weight * 2
        elif term in body:
            score += weight
    if candidate.source.country == "EC" and "ecuador" in body:
        score += 3
    score -= sum(10 for term in EXCLUSION_TERMS if term in title)
    return score


def evaluate_candidate(
    candidate: Candidate,
    now: datetime,
    effective_period: EffectivePeriod | None = None,
) -> Decision:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    score = relevance_score(candidate)
    reasons: list[str] = []

    if score < 5:
        return Decision(VerificationStatus.REJECTED, 0, ("contenido no relacionado",), score)

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

