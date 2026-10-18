import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from fuelintel.filters import is_fast_fuel_signal, relevance_score
from fuelintel.models import (
    Candidate,
    EffectivePeriod,
    FuelPrice,
    PriceType,
    SourceSpec,
    VerificationStatus,
)
from fuelintel.pipeline import Pipeline
from fuelintel.storage import Store
from tests.test_core import _CaptureNotifier, _NoEnrichment


EC_NEWS = SourceSpec(
    id="primicias-economia",
    name="Primicias - Economía",
    url="https://example.test/economia",
    kind="html_links",
    trust=75,
    country="EC",
    max_age_hours=36,
)


class FastTriggerTests(unittest.TestCase):
    def candidate(self, title, source=EC_NEWS):
        return Candidate(source=source, title=title, url="https://example.test/a")

    def test_fuel_plus_actor_triggers_without_a_change_word(self):
        self.assertTrue(is_fast_fuel_signal(self.candidate("Noboa habla sobre el diésel")))

    def test_fuel_plus_change_triggers_without_an_actor(self):
        self.assertTrue(is_fast_fuel_signal(self.candidate("La gasolina baja en Ecuador")))

    def test_actor_without_fuel_does_not_trigger(self):
        self.assertFalse(is_fast_fuel_signal(self.candidate("Noboa inaugura una escuela")))

    def test_change_without_fuel_does_not_trigger(self):
        self.assertFalse(is_fast_fuel_signal(self.candidate("El precio del banano sube")))

    def test_incidental_words_do_not_trigger(self):
        self.assertFalse(is_fast_fuel_signal(self.candidate("Archivo extraordinario de supermercados")))

    def test_ec_actor_fuel_story_clears_the_relevance_floor(self):
        self.assertGreaterEqual(relevance_score(self.candidate("Noboa anuncia el nuevo precio de la gasolina")), 5)


class FastPriceChangeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "test.sqlite3")
        self.notifier = _CaptureNotifier()
        self.pipeline = Pipeline(self.store, _NoEnrichment(), self.notifier, timezone.utc)

    def _seed_confirmed_extra(self, price: str):
        self.store.save_price(
            FuelPrice(
                product="Extra",
                price=Decimal(price),
                period=EffectivePeriod(date(2026, 9, 12), date(2026, 10, 11)),
                price_type=PriceType.PUBLICO,
                source_name="ARCH",
                verification_status="CONFIRMED",
            )
        )

    def _media_candidate(self, body: str):
        return Candidate(
            source=EC_NEWS,
            title="Cambian los precios de gasolina Extra y diésel en Ecuador",
            url="https://example.test/nuevo-precio",
            body=body,
            published_at=datetime(2026, 10, 12, 13, 0, tzinfo=timezone.utc),
            detected_at=datetime(2026, 10, 12, 13, 1, tzinfo=timezone.utc),
            content_hash="media-change-report",
        )

    def test_media_report_flags_a_change_against_the_last_confirmed_price(self):
        self._seed_confirmed_extra("3.21")
        result = self.pipeline.process(
            self._media_candidate("Precios del 12 de octubre al 11 de noviembre de 2026. Extra $3.17")
        )
        self.assertEqual(result.status, VerificationStatus.PENDING)
        self.assertTrue(result.notified)
        joined = "\n".join(self.notifier.messages)
        self.assertIn("CAMBIO DETECTADO", joined)
        self.assertIn("antes $3.21", joined)
        self.assertIn("Variación frente al último precio confirmado", joined)

    def test_media_report_without_a_real_change_has_no_change_marker(self):
        self._seed_confirmed_extra("3.17")
        result = self.pipeline.process(
            self._media_candidate("Precios del 12 de octubre al 11 de noviembre de 2026. Extra $3.17")
        )
        joined = "\n".join(self.notifier.messages)
        self.assertNotIn("CAMBIO DETECTADO", joined)
        self.assertNotIn("Variación frente al último precio confirmado", joined)


if __name__ == "__main__":
    unittest.main()
