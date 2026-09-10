from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .ai import DeepSeekEditor
from .calculations import price_change
from .extractors import extract_effective_period, extract_fuel_prices
from .filters import evaluate_candidate, relevance_score
from .formatting import compose_x_prices, format_news_alert, format_price_alert
from .models import Candidate, Decision, VerificationStatus
from .sources import SourcePoller
from .storage import Store
from .telegram import TelegramNotifier


@dataclass(frozen=True, slots=True)
class ProcessResult:
    status: VerificationStatus
    notified: bool
    item_id: int
    reason: str


class Pipeline:
    def __init__(self, store: Store, poller: SourcePoller, notifier: TelegramNotifier, timezone, editor: DeepSeekEditor | None = None):
        self.store = store
        self.poller = poller
        self.notifier = notifier
        self.timezone = timezone
        self.editor = editor

    def process(
        self,
        candidate: Candidate,
        allow_notify: bool = True,
        corroborated_by: str = "",
    ) -> ProcessResult:
        initial_score = relevance_score(candidate)
        if allow_notify and initial_score >= 5 and candidate.raw_kind in {"link", "rss"}:
            candidate = self.poller.enrich(candidate)

        local_now = candidate.detected_at.astimezone(self.timezone)
        period = extract_effective_period(candidate.body or candidate.summary, local_now.date())
        prices = extract_fuel_prices(
            candidate.body or candidate.summary,
            candidate.url,
            candidate.detected_at,
            local_now.date(),
        )
        if prices and prices[0].period:
            period = prices[0].period

        duplicate, duplicate_reason = self.store.is_duplicate(candidate)
        if duplicate:
            return ProcessResult(VerificationStatus.DUPLICATE, False, 0, duplicate_reason)

        decision = evaluate_candidate(candidate, local_now, period)
        corroborated = bool(corroborated_by)
        if decision.status == VerificationStatus.PENDING and (
            corroborated or self.store.has_corroboration(candidate)
        ):
            corroborated = True
            decision = Decision(
                VerificationStatus.VERIFIED,
                max(82, decision.confidence),
                decision.reasons
                + ((f"confirmada también por {corroborated_by}",) if corroborated_by else ("confirmada por una segunda fuente independiente",)),
                decision.relevance_score,
            )

        if prices and period is None:
            decision = Decision(
                VerificationStatus.PENDING,
                min(decision.confidence, 55),
                decision.reasons + ("precios sin periodo de vigencia verificable",),
                decision.relevance_score,
            )
        elif prices and period and not period.contains(local_now.date()):
            decision = Decision(
                VerificationStatus.STALE,
                0,
                decision.reasons + ("los precios no están vigentes hoy",),
                decision.relevance_score,
            )

        item_id = self.store.save_item(candidate, decision)
        if decision.status != VerificationStatus.VERIFIED:
            return ProcessResult(decision.status, False, item_id, "; ".join(decision.reasons))

        notified = False
        if prices:
            changes = {}
            for item in prices:
                period_start = item.period.start.isoformat() if item.period else None
                previous_raw = self.store.previous_price(item.product, period_start)
                previous = Decimal(previous_raw) if previous_raw is not None else None
                changes[item.product] = price_change(item.price, previous)
                self.store.save_price(item)
            if not allow_notify:
                return ProcessResult(decision.status, False, item_id, "línea base vigente guardada sin notificar")
            fallback = format_price_alert(candidate, prices, changes, local_now)
            editorial = self.editor.review(fallback, compose_x_prices(prices, changes)) if self.editor else None
            message = format_price_alert(candidate, prices, changes, local_now, editorial)
            notified = self.notifier.send_text(message)
        else:
            if not allow_notify:
                return ProcessResult(decision.status, False, item_id, "línea base guardada sin notificar")
            fallback = format_news_alert(candidate, local_now, corroborated)
            fallback_x = fallback.split("TEXTO PARA X:\n", 1)[-1].split("\n\nFuente:", 1)[0]
            editorial = self.editor.review(fallback, fallback_x) if self.editor else None
            message = format_news_alert(candidate, local_now, corroborated, editorial)
            notified = self.notifier.send_text(message)

        if notified:
            self.store.mark_notified(item_id)
        return ProcessResult(decision.status, notified, item_id, "verificada")

