from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from fuelintel.calculations import price_change, validate_price
from fuelintel.extractors import extract_effective_period, extract_fuel_prices
from fuelintel.filters import evaluate_candidate
from fuelintel.formatting import compose_x_prices
from fuelintel.models import Candidate, FuelPrice, SourceSpec, VerificationStatus
from fuelintel.pipeline import Pipeline
from fuelintel.storage import Store
from fuelintel.setup_telegram import latest_private_chat
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


class _NoEnrichment:
    def enrich(self, candidate):
        return candidate


class _CaptureNotifier:
    def __init__(self):
        self.messages = []

    def send_text(self, message):
        self.messages.append(message)
        return True


class PipelineSafetyTests(unittest.TestCase):
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
            self.assertEqual(len(notifier.messages), 1)
            self.assertEqual(len(store.recent_prices()), 1)
            repeated = pipeline.process(candidate)
            self.assertEqual(repeated.status, VerificationStatus.DUPLICATE)
            self.assertEqual(len(notifier.messages), 1)

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
