from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .ai import GeminiEditor
from .calculations import price_change
from .charts import generate_reported_price_chart
from .extractors import extract_effective_period, extract_fuel_prices
from .filters import evaluate_candidate, relevance_score
from .formatting import (
    compose_x_prices,
    format_news_alert,
    format_price_alert,
    format_unconfirmed_news_squawk,
    format_unconfirmed_price_alert,
)
from .models import Candidate, Decision, PriceType, VerificationStatus
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
    def __init__(
        self,
        store: Store,
        poller: SourcePoller,
        notifier: TelegramNotifier,
        timezone,
        editor: GeminiEditor | None = None,
        chart_generator=None,
    ):
        self.store = store
        self.poller = poller
        self.notifier = notifier
        self.timezone = timezone
        self.editor = editor
        self.chart_generator = chart_generator

    def process(
        self,
        candidate: Candidate,
        allow_notify: bool = True,
        corroborated_by: str = "",
    ) -> ProcessResult:
        initial_score = relevance_score(candidate)
        must_follow_official_price_pdf = candidate.source.id == "petroecuador-historico"
        if allow_notify and (initial_score >= 5 or must_follow_official_price_pdf) and candidate.raw_kind in {"link", "rss"}:
            candidate = self.poller.enrich(candidate)

        local_now = candidate.detected_at.astimezone(self.timezone)
        sent_local = datetime.now(timezone.utc).astimezone(self.timezone)
        period = extract_effective_period(candidate.body or candidate.summary, local_now.date())
        prices = extract_fuel_prices(
            candidate.body or candidate.summary,
            candidate.url,
            candidate.detected_at,
            local_now.date(),
        )
        if prices:
            official_source = "ARCH" if candidate.source.id.startswith("arch") else (
                "Petroecuador" if "petroecuador" in candidate.source.id else ""
            )
            prices = [replace(item, source_name=item.source_name or official_source) for item in prices]
        if prices and prices[0].period:
            period = prices[0].period

        duplicate, duplicate_reason = self.store.is_duplicate(candidate, retry_unnotified=allow_notify)
        if duplicate:
            return ProcessResult(VerificationStatus.DUPLICATE, False, 0, duplicate_reason)

        decision = evaluate_candidate(candidate, local_now, period)
        # Terminal freshness decisions must never be promoted by corroboration,
        # price extraction or a missing primary source later in the pipeline.
        if decision.status in {VerificationStatus.STALE, VerificationStatus.REJECTED}:
            item_id = self.store.save_item(candidate, decision)
            return ProcessResult(decision.status, False, item_id, "; ".join(decision.reasons))
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
        elif prices and period and period.end < local_now.date():
            decision = Decision(
                VerificationStatus.STALE,
                0,
                decision.reasons + ("los precios no están vigentes hoy",),
                decision.relevance_score,
            )
        elif prices and period and period.start > local_now.date() and (period.start - local_now.date()).days > 2:
            decision = Decision(
                VerificationStatus.PENDING,
                min(decision.confidence, 55),
                decision.reasons + ("el periodo empieza con más de 48 horas de anticipación; requiere revisión",),
                decision.relevance_score,
            )

        if prices and any(item.price_type == PriceType.UNKNOWN for item in prices):
            self.store.record_pipeline_stage("WF_04_DATA_VALIDATOR", "blocked", "price_type UNKNOWN")
            prices = [replace(item, verification_status="PENDING") for item in prices]
            decision = Decision(
                VerificationStatus.PENDING,
                min(decision.confidence, 45),
                decision.reasons + ("price_type UNKNOWN: se guarda para auditoría y no se publica",),
                decision.relevance_score,
            )

        if prices and any(item.source_name not in {"ARCH", "Petroecuador"} for item in prices):
            prices = [replace(item, verification_status="PENDING") for item in prices]
            self.store.record_pipeline_stage("WF_04_DATA_VALIDATOR", "blocked", "fuente primaria no reconocida")
            decision = Decision(
                decision.status if decision.status in {VerificationStatus.STALE, VerificationStatus.REJECTED} else VerificationStatus.PENDING,
                min(decision.confidence, 40),
                decision.reasons + ("el precio no proviene directamente de ARCH o Petroecuador",),
                decision.relevance_score,
            )

        conflict_products = {item.product for item in prices if self.store.price_conflict(item)}
        if conflict_products:
            self.store.record_pipeline_stage(
                "WF_04_DATA_VALIDATOR", "blocked", f"SOURCE_CONFLICT: {', '.join(sorted(conflict_products))}"
            )
            prices = [
                replace(
                    item,
                    source_conflict=item.product in conflict_products,
                    verification_status="PENDING" if item.product in conflict_products else item.verification_status,
                )
                for item in prices
            ]
            decision = Decision(
                decision.status if decision.status in {VerificationStatus.STALE, VerificationStatus.REJECTED} else VerificationStatus.PENDING,
                min(decision.confidence, 40),
                decision.reasons + (f"SOURCE_CONFLICT: {', '.join(sorted(conflict_products))}",),
                decision.relevance_score,
            )

        item_id = self.store.save_item(candidate, decision)
        if decision.status != VerificationStatus.VERIFIED:
            early_warning_sent = False
            if prices and decision.status == VerificationStatus.PENDING:
                prices = [replace(item, verification_status="PENDING") for item in prices]
                for item in prices:
                    self.store.save_price(item)
                    if item.source_conflict:
                        self.store.mark_source_conflict(item)
                # Relevant price reports must wake the operator even when the
                # primary PDF is missing. The wording explicitly blocks publication.
                if allow_notify and candidate.source.trust >= 72 and initial_score >= 5 and (
                    not period or (period.start - local_now.date()).days <= 2
                ):
                    early_warning_sent = self.notifier.send_text(
                        format_unconfirmed_price_alert(candidate, prices, local_now, decision.reasons, sent_local)
                    )
                    if early_warning_sent:
                        self.store.mark_notified(item_id)
                    chart_prices = [item for item in prices if item.period and not item.source_conflict and item.price_type != PriceType.UNKNOWN]
                    if self.chart_generator is not None and chart_prices:
                        provisional_path = Path(self.store.path).parent / "charts" / f"reported-{local_now:%Y%m%d-%H%M%S}.png"
                        try:
                            provisional = generate_reported_price_chart(
                                self.store, chart_prices, candidate.source.name, provisional_path, local_now
                            )
                            if provisional is not None:
                                self.notifier.send_photo(
                                    provisional,
                                    "Alerta preliminar de precios en Ecuador · pendiente de confirmación oficial",
                                )
                        except Exception as error:
                            self.store.record_chart_error("WF_06_CHART_GENERATOR", f"preliminar: {type(error).__name__}: {error}")
            elif (
                allow_notify
                and decision.status == VerificationStatus.PENDING
                and candidate.raw_kind in {"link", "rss", "telegram", "x"}
                and 72 <= candidate.source.trust < 90
                and initial_score >= 12
            ):
                editorial = None
                if self.editor:
                    evidence = (
                        "Traduce y resume esta alerta al español sin añadir hechos. Es una sola fuente aún no corroborada.\n"
                        f"Fuente: {candidate.source.name}\nTexto: {candidate.summary or candidate.title}"
                    )
                    editorial = self.editor.review(evidence, candidate.title)
                early_warning_sent = self.notifier.send_text(
                    format_unconfirmed_news_squawk(candidate, local_now, sent_local, editorial)
                )
            if early_warning_sent:
                self.store.mark_notified(item_id)
            return ProcessResult(decision.status, early_warning_sent, item_id, "; ".join(decision.reasons))

        notified = False
        if prices:
            self.store.record_pipeline_stage("WF_04_DATA_VALIDATOR", "confirmed", "tipos, vigencia y fuentes validados")
            changes = {}
            for item in prices:
                period_start = item.period.start.isoformat() if item.period else None
                previous_raw = self.store.previous_price(item.product, period_start)
                previous = Decimal(previous_raw) if previous_raw is not None else None
                changes[item.product] = price_change(item.price, previous)
                self.store.save_price(item)
            if not allow_notify:
                return ProcessResult(decision.status, False, item_id, "línea base vigente guardada sin notificar")
            chart = None
            if self.chart_generator:
                chart_path = Path(self.store.path).parent / "charts" / f"fuel-{local_now:%Y%m%d-%H%M%S}.png"
                try:
                    chart = self.chart_generator(self.store, chart_path, local_now)
                    if chart is None:
                        raise RuntimeError("no hay datos confirmados suficientes para el gráfico")
                    self.store.record_pipeline_stage("WF_06_CHART_GENERATOR", "completed", str(chart))
                except Exception as error:
                    self.store.record_chart_error("WF_06_CHART_GENERATOR", f"{type(error).__name__}: {error}")
                    chart = None
            fallback = format_price_alert(candidate, prices, changes, local_now)
            editorial = self.editor.review(fallback, compose_x_prices(prices, changes)) if self.editor else None
            message = format_price_alert(candidate, prices, changes, local_now, editorial, sent_local)
            # A confirmed price alert is visual-first: chart, then context,
            # then the original official PDF as its own Telegram document.
            if chart is not None:
                try:
                    photo_sent = self.notifier.send_photo(
                        chart,
                        f"Precios de combustible en Ecuador · {sent_local:%d/%m/%Y %H:%M} · hora de Ecuador (UTC−5)",
                    )
                    if not photo_sent:
                        self.store.record_chart_error("WF_06_CHART_GENERATOR", "Telegram no confirmó la imagen")
                except Exception as error:
                    self.store.record_chart_error("WF_06_CHART_GENERATOR", f"Telegram: {type(error).__name__}")
            notified = self.notifier.send_text(message)
            if candidate.raw_kind == "pdf" or candidate.url.lower().split("?", 1)[0].endswith(".pdf"):
                try:
                    self.notifier.send_document_url(
                        candidate.url,
                        "Documento oficial original · revisa el periodo, producto, unidad y tipo de precio.",
                    )
                except Exception as error:
                    self.store.record_pipeline_stage(
                        "WF_08_ERROR_MONITOR", "document_error", f"{type(error).__name__}: {error}"
                    )
        else:
            if not allow_notify:
                return ProcessResult(decision.status, False, item_id, "línea base guardada sin notificar")
            fallback = format_news_alert(candidate, local_now, corroborated, sent_local=sent_local)
            fallback_x = fallback.split("TEXTO PARA X:\n", 1)[-1].split("\n\nFuente:", 1)[0]
            editorial = self.editor.review(fallback, fallback_x) if self.editor else None
            message = format_news_alert(candidate, local_now, corroborated, editorial, sent_local)
            notified = self.notifier.send_text(message)

        if notified:
            self.store.mark_notified(item_id)
        return ProcessResult(decision.status, notified, item_id, "verificada")
