from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .ai import Editorial, GeminiEditor, extractive_summary, fact_check_summary
from .calculations import price_change
from .charts import generate_reported_price_chart
from .extractors import extract_effective_period, extract_fuel_prices
from .filters import evaluate_candidate, pending_topic, relevance_score
from .formatting import (
    TelegramSections,
    build_news_sections,
    build_price_sections,
    build_unconfirmed_price_sections,
    compose_x_prices,
    format_news_alert,
    format_price_alert,
    format_unconfirmed_news_squawk,
    format_unconfirmed_price_alert,
)
from .forecasting import forecast_next_price, format_forecast_message
from .knowledge import ecuador_rules_text
from .models import Candidate, Decision, PriceType, VerificationStatus
from .sources import SourcePoller
from .storage import Store
from .telegram import TelegramNotifier
from .text import canonical_url
from .translate import looks_spanish, translate_to_spanish


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
        market_chart_generator=None,
        ecuador_only: bool = False,
    ):
        self.store = store
        self.poller = poller
        self.notifier = notifier
        self.timezone = timezone
        self.editor = editor
        self.chart_generator = chart_generator
        self.market_chart_generator = market_chart_generator
        self.ecuador_only = ecuador_only

    def _send_event_chart(self, item_id: int, candidate: Candidate, local_now: datetime) -> bool:
        """Send at most one context chart for a material story, before its text."""
        if not self.market_chart_generator or self.store.section_delivered(item_id, "event-chart"):
            return False
        destination = Path(self.store.path).parent / "charts" / f"event-{local_now:%Y%m%d-%H%M%S}.png"
        try:
            chart = self.market_chart_generator(
                f"{candidate.title} {candidate.summary} {candidate.body}", destination
            )
            if chart is None:
                return False
            accepted = self.notifier.send_photo(
                chart,
                "Contexto de mercado observado · no demuestra causalidad · hora de Ecuador (UTC−5)",
            )
            if accepted:
                self.store.mark_section_delivered(item_id, "event-chart")
            return accepted
        except Exception as error:
            self.store.record_chart_error("WF_06_CHART_GENERATOR", f"evento: {type(error).__name__}: {error}")
            return False

    def _analysis_context(self, candidate: Candidate) -> str:
        """Assemble locally verified data so the editor analyses instead of paraphrasing."""
        lines: list[str] = []
        try:
            latest: dict[str, object] = {}
            for row in self.store.recent_prices():
                latest[row["product"]] = row
            if latest:
                lines.append("Precios oficiales de Ecuador registrados por el sistema:")
                for product, row in latest.items():
                    period = ""
                    if row["effective_from"] and row["effective_to"]:
                        period = f" (vigente del {row['effective_from']} al {row['effective_to']})"
                    lines.append(f"- {product}: ${row['price']} por galón{period}")
        except Exception:
            pass
        try:
            related = self.store.recent_notified_market_news(hours=48, limit=4)
            others = [
                story
                for story in related
                if canonical_url(str(story.get("canonical_url") or "")) != canonical_url(candidate.url)
            ]
            if others:
                lines.append("Otras noticias recientes ya verificadas (contexto, no causa):")
                lines.extend(f"- Según {story['source_name']}: {story['title']}" for story in others[:3])
        except Exception:
            pass
        return "\n".join(lines)

    def _review(self, evidence: str, fallback_x: str, purpose: str, context: str = "", rules: str = ""):
        if not self.editor:
            return None
        if context:
            evidence = (
                f"{evidence}\n\nDATOS INTERNOS VERIFICADOS (puedes citarlos; no inventes otros):\n{context}"
            )
        if rules:
            evidence += (
                "\n\nREGLAS FIJAS DEL DOMINIO (no citables; guían la interpretación. "
                "Prohibido anunciar un cambio de precio en Ecuador por un movimiento del crudo):\n"
                f"{rules}"
            )
        editorial = self.editor.review(evidence, fallback_x)
        self.store.record_ai_run(
            self.editor.model,
            purpose,
            editorial is not None,
            self.editor.last_error,
        )
        return editorial

    def _ensure_spanish(self, candidate: Candidate) -> Candidate:
        """Translate any non-Spanish title or summary so nothing foreign reaches the user.

        Title and body are checked separately: a Spanish headline over an English
        quotation must not hide an untranslated body.
        """
        body_text = candidate.summary or candidate.body or ""
        title_needs = not looks_spanish(candidate.title or "")
        body_needs = not looks_spanish(body_text)
        if not (title_needs or body_needs):
            return candidate
        title = translate_to_spanish(candidate.title, self.editor) if title_needs else None
        body = translate_to_spanish(body_text, self.editor) if body_needs else None
        translated = bool((title and title.translated) or (body and body.translated))
        self.store.record_pipeline_stage(
            "WF_05_TRANSLATOR",
            "translated" if translated else "untranslated",
            f"title={title.engine if title else 'es'} body={body.engine if body else 'es'}",
        )
        # Never surface a foreign original: if no engine produced a translation,
        # keep a Spanish notice and the link instead of the raw text.
        new_title = candidate.title
        if title_needs:
            new_title = title.text if title.translated else "Noticia en otro idioma; traducción no disponible"
        new_summary = candidate.summary
        if body_needs:
            new_summary = body.text if body.translated else (
                "El texto original está en otro idioma. Abre el enlace para leerlo; "
                "la traducción automática no está disponible en este momento."
            )
        return replace(candidate, title=new_title, summary=new_summary)

    def _ensure_summary_fact_checked(self, candidate: Candidate, editorial: Editorial | None) -> Editorial | None:
        """Verifica que el resumen no invente datos; si falla, usa resumen extractivo de la fuente."""
        if editorial is None:
            return None
        if editorial.summary:
            source_text = f"{candidate.title}. {candidate.summary or candidate.body}"
            ok, reason, missing = fact_check_summary(editorial.summary, source_text)
            if not ok:
                self.store.record_pipeline_stage(
                    "WF_05_TRANSLATOR", "summary_fact_check_failed", reason
                )
                summary = extractive_summary(source_text)
                return replace(editorial, summary=summary)
        return editorial

    def _send_sections(self, item_id: int, sections: TelegramSections) -> bool:
        """Send each section separately and resume without duplicating prior parts."""
        delivered = True
        for index, message in enumerate(sections.messages()):
            section = f"text-{index}"
            if self.store.section_delivered(item_id, section):
                continue
            try:
                accepted = self.notifier.send_text(message)
            except Exception as error:
                # A failure on one section must not stop the others; the failed
                # part stays undelivered and is retried, the rest still goes out.
                self.store.record_pipeline_stage(
                    "WF_08_ERROR_MONITOR", "telegram_section_error", f"{section}: {type(error).__name__}: {error}"
                )
                delivered = False
                continue
            if not accepted:
                delivered = False
                continue
            self.store.mark_section_delivered(item_id, section)
        return delivered

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

        decision = evaluate_candidate(candidate, local_now, period, ecuador_only=self.ecuador_only)
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

        item_id = self.store.save_item(candidate, decision, pending_topic(candidate))
        if decision.status != VerificationStatus.VERIFIED:
            early_warning_sent = False
            if prices and decision.status == VerificationStatus.PENDING:
                prices = [replace(item, verification_status="PENDING") for item in prices]
                # Detect the change against the last confirmed price before saving
                # the provisional report, so the operator sees the movement early.
                media_changes = {}
                for item in prices:
                    period_start = item.period.start.isoformat() if item.period else None
                    previous_raw = self.store.previous_price(item.product, period_start)
                    previous = Decimal(previous_raw) if previous_raw is not None else None
                    media_changes[item.product] = price_change(item.price, previous)
                for item in prices:
                    self.store.save_price(item)
                    if item.source_conflict:
                        self.store.mark_source_conflict(item)
                # Relevant price reports must wake the operator even when the
                # primary PDF is missing. The wording explicitly blocks publication.
                # Only reports that carry real figures AND a declared vigencia
                # qualify; a hard cap allows one watch ping per topic per window.
                if (
                    allow_notify
                    and candidate.source.trust >= 72
                    and initial_score >= 5
                    and period
                    and (period.start - local_now.date()).days <= 2
                    and not self.store.pending_topic_notified(pending_topic(candidate), candidate.detected_at)
                ):
                    early_warning_sent = self._send_sections(
                        item_id,
                        build_unconfirmed_price_sections(
                            candidate, prices, local_now, decision.reasons, sent_local, media_changes
                        ),
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
                and not self.store.pending_topic_notified(pending_topic(candidate), candidate.detected_at)
            ):
                candidate = self._ensure_spanish(candidate)
                editorial = None
                if self.editor:
                    evidence = (
                        "Traduce y resume esta alerta al español sin añadir hechos. Es una sola fuente aún no corroborada.\n"
                        f"Fuente: {candidate.source.name}\nTexto: {candidate.summary or candidate.title}"
                    )
                    editorial = self._review(
            evidence, candidate.title, "early-news", self._analysis_context(candidate),
            rules=self.store.get_meta("ecuador_rules") or ecuador_rules_text(),
        )
                    editorial = self._ensure_summary_fact_checked(candidate, editorial)
                if initial_score >= 18:
                    self._send_event_chart(item_id, candidate, local_now)
                early_warning_sent = self._send_sections(
                    item_id,
                    build_news_sections(
                        candidate, local_now, False, editorial, sent_local, confirmed=False
                    ),
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
            editorial = self._review(
                fallback,
                compose_x_prices(prices, changes, candidate.source.name),
                "official-prices",
                self._analysis_context(candidate),
                rules=self.store.get_meta("ecuador_rules") or ecuador_rules_text(),
            )
            forecast_messages: list[str] = []
            for item in prices:
                result = forecast_next_price(
                    item.product,
                    self.store.price_history(item.product, item.price_type.value),
                )
                self.store.record_forecast(result)
                message = format_forecast_message(result, sent_local)
                if message:
                    forecast_messages.append(message)
            sections = build_price_sections(
                candidate,
                prices,
                changes,
                local_now,
                editorial,
                sent_local,
                forecast="\n\n".join(forecast_messages),
            )
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
            notified = self._send_sections(item_id, sections)
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
            candidate = self._ensure_spanish(candidate)
            fallback = format_news_alert(candidate, local_now, corroborated, sent_local=sent_local)
            fallback_x = fallback.split("TEXTO PARA X:\n", 1)[-1].split("\n\nFuente:", 1)[0]
            editorial = self._review(
            fallback, fallback_x, "verified-news", self._analysis_context(candidate),
            rules=self.store.get_meta("ecuador_rules") or ecuador_rules_text(),
        )
            editorial = self._ensure_summary_fact_checked(candidate, editorial)
            sections = build_news_sections(candidate, local_now, corroborated, editorial, sent_local)
            if initial_score >= 18:
                self._send_event_chart(item_id, candidate, local_now)
            notified = self._send_sections(item_id, sections)

        if notified:
            self.store.mark_notified(item_id)
        return ProcessResult(decision.status, notified, item_id, "verificada")
