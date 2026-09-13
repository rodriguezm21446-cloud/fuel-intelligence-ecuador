from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any


class VerificationStatus(StrEnum):
    VERIFIED = "verified"
    PENDING = "pending"
    REJECTED = "rejected"
    STALE = "stale"
    DUPLICATE = "duplicate"


class PriceType(StrEnum):
    PUBLICO = "PUBLICO"
    TERMINAL = "TERMINAL"
    REFERENCIAL = "REFERENCIAL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class SourceSpec:
    id: str
    name: str
    url: str
    kind: str
    trust: int
    country: str = "EC"
    critical: bool = False
    poll_seconds: int = 60
    idle_poll_seconds: int = 300
    max_age_hours: int = 36
    burst_days: tuple[int, ...] = ()
    enabled: bool = True

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SourceSpec":
        values = dict(raw)
        values["burst_days"] = tuple(values.get("burst_days", ()))
        return cls(**values)

    def interval_for(self, now: datetime) -> int:
        if not self.burst_days or now.day in self.burst_days:
            return max(30, self.poll_seconds)
        return max(30, self.idle_poll_seconds)


@dataclass(slots=True)
class Candidate:
    source: SourceSpec
    title: str
    url: str
    summary: str = ""
    body: str = ""
    published_at: datetime | None = None
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    content_hash: str = ""
    raw_kind: str = "html"


@dataclass(frozen=True, slots=True)
class EffectivePeriod:
    start: date
    end: date

    def contains(self, value: date) -> bool:
        return self.start <= value <= self.end


@dataclass(frozen=True, slots=True)
class FuelPrice:
    product: str
    price: Decimal
    currency: str = "USD"
    unit: str = "galón"
    period: EffectivePeriod | None = None
    source_url: str = ""
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    verified_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    price_type: PriceType = PriceType.UNKNOWN
    source_name: str = ""
    decree_resolution: str = ""
    source_conflict: bool = False
    verification_status: str = "CONFIRMED"


@dataclass(frozen=True, slots=True)
class Decision:
    status: VerificationStatus
    confidence: int
    reasons: tuple[str, ...]
    relevance_score: int = 0


@dataclass(frozen=True, slots=True)
class PriceChange:
    current: Decimal
    previous: Decimal | None
    absolute: Decimal | None
    percent: Decimal | None
