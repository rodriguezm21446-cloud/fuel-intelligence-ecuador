from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from fuelintel.calculations import price_change, validate_price
from fuelintel.extractors import extract_effective_period, extract_fuel_prices
from fuelintel.filters import evaluate_candidate, relevance_score
from fuelintel.formatting import (
    build_news_sections,
    compose_x_prices,
    format_ecuador_time,
    format_news_alert,
    format_unconfirmed_news_squawk,
)
from fuelintel.models import Candidate, FuelPrice, SourceSpec, VerificationStatus
from fuelintel.pipeline import Pipeline
from fuelintel.storage import Store
from fuelintel.setup_telegram import latest_private_chat
from fuelintel.sources import parse_telegram_channel
from fuelintel.text import canonical_url, title_similarity


NOW = datetime(2026, 9, 8, 15, 0, tzinfo=timezone.utc)
OFFICIAL = SourceSpec(
    id="arch",
    name="ARCH",
    url="https://controlhidrocarburos.gob.ec/precios",
    kind="html",
    trust=100,
    max_age_hours=744,
)


class ExtractionTests(unittest.TestCase):
    def test_period_crossing_month(self):
        period = extract_effective_period(
            "Precios vigentes del 12 de agosto al 11 de septiembre de 2026",
            date(2026, 9, 8),
        )
        self.assertIsNotNone(period)
        self.assertEqual(period.start, date(2026, 8, 12))
        self.assertEqual(period.end, date(2026, 9, 11))

    def test_period_crossing_year(self):
        period = extract_effective_period(
            "12 de diciembre al 11 de enero de 2027",
            date(2027, 1, 2),
        )
        self.assertEqual(period.start, date(2026, 12, 12))
        self.assertEqual(period.end, date(2027, 1, 11))

    def test_extract_prices(self):
        text = """
        Precios del 12 de agosto al 11 de septiembre de 2026.
        Extra / Ecopaís $2.40 Diésel Premium $1.037 Súper referencial $2.98
        """
        prices = extract_fuel_prices(text, OFFICIAL.url, NOW, date(2026, 9, 8))
        values = {item.product: item.price for item in prices}
        self.assertEqual(values["Extra"], Decimal("2.40"))
        self.assertEqual(values["Ecopaís"], Decimal("2.40"))
        self.assertEqual(values["Diésel Premium"], Decimal("1.037"))
        self.assertEqual(values["Súper"], Decimal("2.98"))

    def test_extracts_price_transitions_reported_by_ecuador_media(self):
        text = """
        Los precios rigen del 12 de septiembre al 11 de octubre de 2026.
        Las gasolinas Extra y Ecopaís pasan de USD 3,24 a USD 3,21 por galón.
        El diésel Premium baja de USD 3,18 a USD 3,15.
        La gasolina Súper subirá a USD 4,89.
        """
        prices = extract_fuel_prices(text, "https://medio.example/nota", NOW, date(2026, 9, 11))
        values = {item.product: item.price for item in prices}
        self.assertEqual(values["Extra"], Decimal("3.21"))
        self.assertEqual(values["Ecopaís"], Decimal("3.21"))
        self.assertEqual(values["Diésel Premium"], Decimal("3.15"))
        self.assertEqual(values["Súper"], Decimal("4.89"))

    def test_does_not_mistake_old_super_price_for_new_price(self):
        text = """
        La gasolina Súper, que actualmente se comercializa en alrededor de USD 4,64,
        tendrá un incremento de entre 12 y 15 centavos. Extra y Ecopaís bajarán.
        """
        prices = extract_fuel_prices(text, "https://medio.example/nota", NOW, date(2026, 9, 11))
        self.assertNotIn("Súper", {item.product for item in prices})

    def test_extracts_only_automotive_terminal_rows_from_official_pdf(self):
        text = """
        PERIODO DE VIGENCIA: DEL 12 DE AGOSTO AL 11 DE SEPTIEMBRE DE 2026
        PRECIO EN TERMINAL (Incluye IVA)
        GASOLINA EXTRA PETROLERO Galones $ 3,736914
        DIESEL PREMIUM INDUSTRIAL Galones $ 4,707456
        GASOLINA EXTRA AUTOMOTRIZ Galones $ 3,022927
        GASOLINA EXTRA CON ETANOL AUTOMOTRIZ Galones $ 3,022927
        DIESEL PREMIUM (AUTOMOTRIZ) Galones $ 3,032879
        GASOLINA SUPER PREMIUM 95 AUTOMOTRIZ Galones $ 4,087589
        """
        prices = extract_fuel_prices(text, OFFICIAL.url, NOW, date(2026, 9, 8))
        values = {item.product: item.price for item in prices}
        self.assertEqual(values["Extra (terminal automotriz)"], Decimal("3.022927"))
        self.assertEqual(values["Ecopaís (terminal automotriz)"], Decimal("3.022927"))
        self.assertEqual(values["Diésel Premium (terminal automotriz)"], Decimal("3.032879"))
        self.assertEqual(values["Súper 95 (terminal automotriz)"], Decimal("4.087589"))
        self.assertNotIn("Extra", values)


class VerificationTests(unittest.TestCase):
    def candidate(self, published_at=NOW):
        return Candidate(
            source=OFFICIAL,
            title="Nuevos precios de gasolina Extra y diésel",
            url="https://example.test/noticia",
            summary="Ecuador actualiza el precio de combustibles",
            published_at=published_at,
            detected_at=NOW,
            content_hash="abc",
        )

    def test_official_recent_is_verified(self):
        decision = evaluate_candidate(self.candidate(), NOW)
        self.assertEqual(decision.status, VerificationStatus.VERIFIED)

    def test_old_news_is_stale(self):
        source = SourceSpec(
            id="news", name="News", url="https://example.test", kind="rss", trust=75, max_age_hours=36
        )
        candidate = Candidate(
            source=source,
            title="Precio de gasolina",
            url="https://example.test/old",
            published_at=NOW - timedelta(days=10),
            detected_at=NOW,
            content_hash="old",
        )
        self.assertEqual(evaluate_candidate(candidate, NOW).status, VerificationStatus.STALE)

    def test_price_math_is_decimal(self):
        change = price_change(Decimal("2.400"), Decimal("2.350"))
        self.assertEqual(change.absolute, Decimal("0.050"))
        self.assertEqual(change.percent, Decimal("2.13"))
        self.assertTrue(validate_price(Decimal("2.4"))[0])
        self.assertFalse(validate_price(Decimal("98"))[0])

    def test_incidental_words_do_not_trigger_fuel_alert(self):
        candidate = Candidate(
            source=OFFICIAL,
            title="Archivo extraordinario de supermercados",
            url="https://example.test/unrelated",
        )
        self.assertEqual(relevance_score(candidate), 0)


class DeduplicationTests(unittest.TestCase):
    def test_tracking_params_removed(self):
        self.assertEqual(
            canonical_url("https://example.test/a?utm_source=x&id=2"),
            "https://example.test/a?id=2",
        )

    def test_title_similarity(self):
        score = title_similarity(
            "Suben los precios de gasolina Extra y Ecopaís en Ecuador",
            "Precio de la gasolina Extra y Ecopaís sube en Ecuador",
        )
        self.assertGreater(score, 0.5)

    def test_exact_item_is_not_repeated(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "db.sqlite3")
            candidate = Candidate(
                source=OFFICIAL,
                title="Precio de gasolina Extra",
                url="https://example.test/a",
                published_at=NOW,
                detected_at=NOW,
                content_hash="same-hash",
            )
            decision = evaluate_candidate(candidate, NOW)
            store.save_item(candidate, decision)
            self.assertTrue(store.is_duplicate(candidate)[0])
            queued = store.pending_sync()
            self.assertEqual(len(queued), 1)
            self.assertEqual(queued[0]["entity_type"], "items")


class FormattingTests(unittest.TestCase):
    def test_x_text_never_exceeds_280(self):
        prices = [
            FuelPrice("Extra", Decimal("2.400")),
            FuelPrice("Ecopaís", Decimal("2.400")),
            FuelPrice("Diésel Premium", Decimal("1.037")),
            FuelPrice("Súper", Decimal("2.980")),
        ]
        changes = {item.product: price_change(item.price, item.price) for item in prices}
        self.assertLessEqual(len(compose_x_prices(prices, changes)), 280)

    def test_x_price_text_preserves_current_previous_and_change_figures(self):
        prices = [
            FuelPrice("Extra/Ecopaís", Decimal("2.720")),
            FuelPrice("Diésel", Decimal("2.900")),
            FuelPrice("Súper", Decimal("4.500")),
        ]
        previous = {"Extra/Ecopaís": Decimal("2.650"), "Diésel": Decimal("2.850"), "Súper": Decimal("4.400")}
        changes = {item.product: price_change(item.price, previous[item.product]) for item in prices}
        message = compose_x_prices(prices, changes)
        self.assertLessEqual(len(message), 300)
        for expected in ("2.72", "2.65", "0.070", "2.9", "2.85", "0.050", "4.5", "4.4", "0.100"):
            self.assertIn(expected, message)

    def test_every_alert_identifies_ecuador_time_and_delivery_time(self):
        local = datetime(2026, 9, 11, 9, 15, tzinfo=timezone(timedelta(hours=-5)))
        self.assertEqual(
            format_ecuador_time(local),
            "11/09/2026 09:15:00 — hora de Ecuador (UTC−5)",
        )
        candidate = Candidate(
            source=OFFICIAL,
            title="Nuevo precio del diésel",
            url="https://example.test/noticia",
            summary="El precio cambia desde el 12 de septiembre.",
        )
        message = format_news_alert(candidate, local, False, sent_local=local + timedelta(hours=2))
        self.assertIn("Detectado: 11/09/2026 09:15:00 — hora de Ecuador (UTC−5)", message)
        self.assertIn("Enviado: 11/09/2026 11:15:00 — hora de Ecuador (UTC−5)", message)

    def test_single_source_squawk_never_claims_a_confirmed_cause(self):
        local = datetime(2026, 9, 11, 9, 15, tzinfo=timezone(timedelta(hours=-5)))
        source = SourceSpec(
            id="cnbc-energy-rss", name="CNBC - Energy RSS", url="https://example.test/rss",
            kind="rss", trust=82, country="INTL"
        )
        candidate = Candidate(
            source=source,
            title="Oil rises after a refinery outage",
            url="https://example.test/oil",
            summary="Brent rose while traders assessed the reported refinery outage.",
        )
        message = format_unconfirmed_news_squawk(candidate, local, local)
        self.assertIn("UNA SOLA FUENTE", message)
        self.assertIn("Aún no corroborada", message)
        self.assertIn("CANAL HACIA ECUADOR", message)

    def test_short_squawk_and_context_are_separate(self):
        local = datetime(2026, 9, 11, 9, 15, tzinfo=timezone(timedelta(hours=-5)))
        candidate = Candidate(
            source=OFFICIAL,
            title="Petroecuador publica nuevos precios de gasolina",
            url="https://example.test/precios",
            summary="Los valores rigen desde el 12 de septiembre.",
        )
        sections = build_news_sections(candidate, local, False, sent_local=local)
        self.assertLessEqual(len(sections.squawk), 300)
        self.assertNotEqual(sections.squawk, sections.context)
        self.assertNotIn(candidate.url, sections.squawk)
        self.assertIn(candidate.url, sections.context)


class _NoEnrichment:
    def enrich(self, candidate):
        return candidate


class _CaptureNotifier:
    def __init__(self):
        self.messages = []
        self.events = []

    def send_text(self, message):
        self.messages.append(message)
        self.events.append(("text", message))
        return True

    def send_photo(self, path, caption):
        self.events.append(("photo", caption))
        return True

    def send_document_url(self, url, caption):
        self.events.append(("document", url))
        return True


class PipelineSafetyTests(unittest.TestCase):
    def test_high_impact_international_story_sends_early_squawk(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "db.sqlite3")
            notifier = _CaptureNotifier()
            source = SourceSpec(
                id="cnbc-energy-rss", name="CNBC - Energy RSS", url="https://example.test/rss",
                kind="rss", trust=82, country="INTL", max_age_hours=18
            )
            pipeline = Pipeline(store, _NoEnrichment(), notifier, timezone.utc)
            candidate = Candidate(
                source=source,
                title="Brent oil surges after pipeline outage threatens supply",
                url="https://example.test/brent",
                summary="Crude oil supply faces a reported disruption.",
                published_at=NOW,
                detected_at=NOW,
                content_hash="international-squawk",
                raw_kind="rss",
            )
            result = pipeline.process(candidate)
            self.assertEqual(result.status, VerificationStatus.PENDING)
            self.assertTrue(result.notified)
            self.assertEqual(len(notifier.messages), 2)
            self.assertLessEqual(len(notifier.messages[0]), 300)
            self.assertIn("pendiente de corroboración", notifier.messages[1])

    def test_high_impact_story_sends_relevant_chart_before_separate_texts(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = Store(root / "db.sqlite3")
            notifier = _CaptureNotifier()
            chart = root / "event.png"
            chart.write_bytes(b"png")
            source = SourceSpec(
                id="oilprice-main-rss", name="OilPrice", url="https://example.test/rss",
                kind="rss", trust=78, country="INTL", max_age_hours=18,
            )
            pipeline = Pipeline(
                store, _NoEnrichment(), notifier, timezone.utc,
                market_chart_generator=lambda *_: chart,
            )
            candidate = Candidate(
                source=source,
                title="Diesel and gasoline surge after refinery outage and pipeline attack",
                url="https://example.test/energy",
                summary="Oil supply and ULSD markets react to a reported refinery outage.",
                published_at=NOW,
                detected_at=NOW,
                content_hash="event-chart-first",
                raw_kind="rss",
            )
            result = pipeline.process(candidate)
            self.assertTrue(result.notified)
            self.assertEqual([kind for kind, _ in notifier.events], ["photo", "text", "text"])
            self.assertIn("no demuestra causalidad", notifier.events[0][1])

    def test_domestic_price_report_wakes_operator_but_is_not_official(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "db.sqlite3")
            notifier = _CaptureNotifier()
            news = SourceSpec(
                id="primicias-economia",
                name="Primicias - Economía",
                url="https://example.test/economia",
                kind="html_links",
                trust=75,
                country="EC",
                max_age_hours=36,
            )
            pipeline = Pipeline(store, _NoEnrichment(), notifier, timezone.utc)
            candidate = Candidate(
                source=news,
                title="Cambian los precios de gasolina Extra y diésel en Ecuador",
                url="https://example.test/nuevo-precio",
                body="Precios del 12 de septiembre al 11 de octubre de 2026. Extra $3.21 Diésel Premium $3.15",
                published_at=datetime(2026, 9, 11, 20, 0, tzinfo=timezone.utc),
                detected_at=datetime(2026, 9, 11, 20, 1, tzinfo=timezone.utc),
                content_hash="early-price-news",
            )
            result = pipeline.process(candidate)
            self.assertEqual(result.status, VerificationStatus.PENDING)
            self.assertTrue(result.notified)
            self.assertEqual(len(notifier.messages), 2)
            self.assertIn("Aún no es precio oficial", notifier.messages[0])
            self.assertIn("No publicar todavía", notifier.messages[1])

    def test_expired_price_is_never_sent_or_saved_as_current(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "db.sqlite3")
            notifier = _CaptureNotifier()
            pipeline = Pipeline(store, _NoEnrichment(), notifier, timezone.utc)
            candidate = Candidate(
                source=OFFICIAL,
                title="Precios de gasolina y diésel",
                url="https://example.test/precios",
                body="Precios del 12 de enero al 11 de febrero de 2026. Extra $2.40",
                detected_at=NOW,
                content_hash="expired-price",
            )
            result = pipeline.process(candidate)
            self.assertEqual(result.status, VerificationStatus.STALE)
            self.assertEqual(notifier.messages, [])
            self.assertEqual(store.recent_prices(), [])

    def test_current_official_price_is_sent_once(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "db.sqlite3")
            notifier = _CaptureNotifier()
            pipeline = Pipeline(store, _NoEnrichment(), notifier, timezone.utc)
            candidate = Candidate(
                source=OFFICIAL,
                title="Precios de gasolina y diésel",
                url="https://example.test/precios",
                body="Precios del 12 de agosto al 11 de septiembre de 2026. Extra $2.40",
                detected_at=NOW,
                content_hash="current-price",
            )
            result = pipeline.process(candidate)
            self.assertEqual(result.status, VerificationStatus.VERIFIED)
            self.assertEqual(len(notifier.messages), 2)
            self.assertEqual(len(store.recent_prices()), 1)
            repeated = pipeline.process(candidate)
            self.assertEqual(repeated.status, VerificationStatus.DUPLICATE)
            self.assertEqual(len(notifier.messages), 2)

    def test_tomorrows_official_period_is_sent_when_pdf_appears_today(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "db.sqlite3")
            notifier = _CaptureNotifier()
            pipeline = Pipeline(store, _NoEnrichment(), notifier, timezone.utc)
            candidate = Candidate(
                source=OFFICIAL,
                title="Precios oficiales desde el 12 de septiembre",
                url="https://example.test/precios-septiembre.pdf",
                body="Precios del 12 de septiembre al 11 de octubre de 2026. Extra $3.21",
                detected_at=datetime(2026, 9, 11, 15, 0, tzinfo=timezone.utc),
                content_hash="next-period-price",
            )
            result = pipeline.process(candidate)
            self.assertEqual(result.status, VerificationStatus.VERIFIED)
            self.assertEqual(len(notifier.messages), 2)
            self.assertIn("12/09–11/10", notifier.messages[0])
            self.assertEqual([kind for kind, _ in notifier.events], ["text", "text", "document"])

    def test_confirmed_price_sends_chart_before_context_and_pdf(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = Store(root / "db.sqlite3")
            notifier = _CaptureNotifier()
            chart_path = root / "chart.png"
            chart_path.write_bytes(b"png")
            pipeline = Pipeline(
                store,
                _NoEnrichment(),
                notifier,
                timezone.utc,
                chart_generator=lambda *_: chart_path,
            )
            candidate = Candidate(
                source=OFFICIAL,
                title="Precios oficiales desde el 12 de septiembre",
                url="https://example.test/precios-septiembre.pdf",
                body="Precios del 12 de septiembre al 11 de octubre de 2026. Extra $3.21",
                detected_at=datetime(2026, 9, 11, 15, 0, tzinfo=timezone.utc),
                content_hash="visual-first-price",
                raw_kind="pdf",
            )
            result = pipeline.process(candidate)
            self.assertEqual(result.status, VerificationStatus.VERIFIED)
            self.assertEqual([kind for kind, _ in notifier.events], ["photo", "text", "text", "document"])

    def test_far_future_official_period_is_held_for_review(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "db.sqlite3")
            notifier = _CaptureNotifier()
            pipeline = Pipeline(store, _NoEnrichment(), notifier, timezone.utc)
            candidate = Candidate(
                source=OFFICIAL,
                title="Supuestos precios futuros",
                url="https://example.test/futuro.pdf",
                body="Precios del 12 de octubre al 11 de noviembre de 2026. Extra $9.99",
                detected_at=datetime(2026, 9, 11, 15, 0, tzinfo=timezone.utc),
                content_hash="far-future-price",
            )
            result = pipeline.process(candidate)
            self.assertEqual(result.status, VerificationStatus.PENDING)
            self.assertEqual(notifier.messages, [])
            saved = store.recent_prices()
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0]["verification_status"], "PENDING")

    def test_bootstrap_saves_current_price_without_notification(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "db.sqlite3")
            notifier = _CaptureNotifier()
            pipeline = Pipeline(store, _NoEnrichment(), notifier, timezone.utc)
            candidate = Candidate(
                source=OFFICIAL,
                title="Precios de gasolina",
                url="https://example.test/base",
                body="Precios del 12 de agosto al 11 de septiembre de 2026. Extra $2.40",
                detected_at=NOW,
                content_hash="bootstrap-price",
            )
            result = pipeline.process(candidate, allow_notify=False)
            self.assertEqual(result.status, VerificationStatus.VERIFIED)
            self.assertEqual(notifier.messages, [])
            self.assertEqual(len(store.recent_prices()), 1)


if __name__ == "__main__":
    unittest.main()


class TelegramChannelTests(unittest.TestCase):
    def test_public_preview_is_split_into_dated_posts(self):
        source = SourceSpec("telegram-test", "Canal", "https://t.me/s/test", "telegram", 82, country="INTL")
        payload = b'''<div class="tgme_widget_message_wrap">
          <div class="tgme_widget_message" data-post="test/42">
            <div class="tgme_widget_message_text">OIL FALLS<br>AFTER INVENTORY REPORT</div>
            <time datetime="2026-09-13T10:30:00+00:00"></time>
          </div>
        </div>'''
        posts = parse_telegram_channel(source, payload, NOW)
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].url, "https://t.me/test/42")
        self.assertEqual(posts[0].summary, "OIL FALLS AFTER INVENTORY REPORT")
        self.assertEqual(posts[0].published_at, datetime(2026, 9, 13, 10, 30, tzinfo=timezone.utc))

    def test_energy_alerts_score_in_english_and_arabic(self):
        source = SourceSpec("telegram-test", "Canal", "https://t.me/s/test", "telegram", 82, country="INTL")
        english = Candidate(source, "ATTACK ON SAUDI PIPELINE", "https://t.me/test/1")
        arabic = Candidate(source, "هجوم على ناقلة في مضيق هرمز", "https://t.me/test/2")
        self.assertGreaterEqual(relevance_score(english), 12)
        self.assertGreaterEqual(relevance_score(arabic), 12)


class TelegramSetupTests(unittest.TestCase):
    def test_latest_private_chat_ignores_groups(self):
        updates = [
            {"message": {"chat": {"id": -10, "type": "group"}}},
            {"message": {"chat": {"id": 123, "type": "private", "first_name": "Ana"}}},
        ]
        self.assertEqual(
            latest_private_chat(updates),
            {"id": 123, "type": "private", "first_name": "Ana"},
        )
