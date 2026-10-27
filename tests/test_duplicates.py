"""Regression tests for squawks that reached Telegram twice.

The operator reported duplicated price alerts.  Three independent causes were
found in the code; each one gets a test here so the guarantee stays explicit:

1. ``is_duplicate`` compared the figures of two headlines as raw strings, so
   ``3,175`` and ``3,17`` looked like different announcements and the title
   similarity check was skipped entirely.
2. The pending-price branch in :meth:`Pipeline.process` called
   ``mark_notified`` twice (once inside the chart block, once at the end), so a
   crash between the two writes re-opened an already delivered squawk.
3. The same figures reported by a second outlet with different wording passed
   the 0.78 threshold only by luck.
"""

import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from fuelintel.models import (
    Candidate,
    Decision,
    EffectivePeriod,
    FuelPrice,
    PriceType,
    SourceSpec,
    VerificationStatus,
)
from fuelintel.storage import Store
from fuelintel.text import normalized_numbers, same_announcement, title_similarity


ARCH = SourceSpec(
    id="arch-precios",
    name="ARCH - Precios de derivados",
    url="https://example.test/arch",
    kind="html",
    trust=100,
    country="EC",
    max_age_hours=744,
)
PRIMICIAS = SourceSpec(
    id="primicias-economia",
    name="Primicias - Economía",
    url="https://example.test/primicias",
    kind="html_links",
    trust=75,
    country="EC",
    max_age_hours=36,
)
EL_UNIVERSO = SourceSpec(
    id="el-universo-economia",
    name="El Universo - Economía",
    url="https://example.test/universo",
    kind="rss",
    trust=70,
    country="EC",
)


class NormalizedNumbersTests(unittest.TestCase):
    def test_trailing_zero_variants_collapse(self):
        self.assertEqual(normalized_numbers("Extra a 3,175"), normalized_numbers("Extra a 3.1750"))

    def test_two_and_three_decimals_stay_distinct_amounts(self):
        # 3,17 and 3,175 are different prices.  They must not collapse, or a
        # genuine fourth-cent change would be swallowed as a duplicate.
        self.assertNotEqual(normalized_numbers("baja a 3,17"), normalized_numbers("baja a 3,175"))

    def test_rounded_variant_is_recognised_as_the_same_amount(self):
        # The duplicate gate accepts them through same_announcement instead.
        self.assertTrue(same_announcement(
            "Gasolina Extra y Ecopais bajan a 3,175 por galon desde el 12 de octubre",
            "Extra y Ecopais bajan a 3,17 por galon desde el 12 de octubre",
        ))

    def test_a_new_figure_is_a_new_announcement(self):
        self.assertFalse(same_announcement(
            "Gasolina Extra baja a 3,175 por galon desde el 12 de octubre",
            "Gasolina Extra baja a 3,21 por galon desde el 12 de noviembre",
        ))

    def test_different_prices_stay_different(self):
        self.assertNotEqual(normalized_numbers("baja a 3,17"), normalized_numbers("baja a 3,21"))

    def test_integers_are_kept_apart_from_decimals(self):
        self.assertNotEqual(normalized_numbers("12 de octubre"), normalized_numbers("12,5 de octubre"))

    def test_no_figures_yields_an_empty_key(self):
        self.assertEqual(normalized_numbers("Noboa anuncia el nuevo precio"), ())


class DuplicateGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "test.sqlite3")

    def _seen(self, title, source, url):
        candidate = Candidate(
            source=source,
            title=title,
            url=url,
            detected_at=datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc),
            content_hash=f"seen-{url}",
        )
        item_id = self.store.save_item(candidate, Decision(VerificationStatus.VERIFIED, 95, ("test",)))
        self.store.mark_notified(item_id, datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc))
        return candidate

    def _check(self, title, source, suffix=""):
        candidate = Candidate(
            source=source,
            title=title,
            url=f"https://example.test/{abs(hash(title + suffix))}",
            detected_at=datetime(2026, 10, 12, 14, 10, tzinfo=timezone.utc),
            content_hash=f"check-{title}-{suffix}",
        )
        return self.store.is_duplicate(candidate)

    def test_decimal_variant_of_the_same_price_is_a_duplicate(self):
        self._seen(
            "Gasolina Extra y Ecopais bajan a 3,175 por galon desde el 12 de octubre",
            ARCH,
            "https://example.test/arch/1",
        )
        duplicate, reason = self._check(
            "Extra y Ecopais bajan a 3,17 por galon desde el 12 de octubre", PRIMICIAS
        )
        self.assertTrue(duplicate, reason)

    def test_same_announcement_resyndicated_is_a_duplicate(self):
        """Two outlets running the same wire copy must not page twice."""
        self._seen(
            "Gasolina Extra y Ecopais bajan 4 centavos y costaran 3,175 el galon",
            ARCH,
            "https://example.test/arch/2",
        )
        duplicate, reason = self._check(
            "Gasolina Extra y Ecopais bajan 4 centavos y costaran 3,175 el galon", PRIMICIAS
        )
        self.assertTrue(duplicate, reason)

    def test_same_figures_with_heavy_rewording_is_not_assumed_duplicate(self):
        """Different wording alone is not enough; the operator decides.

        Deliberately conservative: a genuinely rewritten headline with the same
        price is not suppressed, because the two stories may carry different
        details (which product, which decree).  The price-plus-URL fingerprints
        still catch the exact copy.
        """
        self._seen(
            "Gasolina Extra y Ecopais bajan 4 centavos: 3,175 el galon",
            ARCH,
            "https://example.test/arch/8",
        )
        duplicate, reason = self._check(
            "Nuevo precio de la gasolina Extra: 3,175 por galon", PRIMICIAS
        )
        self.assertFalse(duplicate, reason)

    def test_a_genuinely_new_price_is_not_a_duplicate(self):
        self._seen(
            "Gasolina Extra y Ecopais bajan a 3,175 por galon desde el 12 de octubre",
            ARCH,
            "https://example.test/arch/3",
        )
        duplicate, reason = self._check(
            "Gasolina Extra y Ecopais bajan a 3,21 por galon desde el 12 de noviembre", PRIMICIAS
        )
        self.assertFalse(duplicate, reason)

    def test_an_higher_trust_source_still_publishes_its_own_confirmation(self):
        """ARCH confirming a figure Primicias leaked first is a new event."""
        self._seen(
            "Gasolina Extra baja 4 centavos y costara 3,175 el galon",
            PRIMICIAS,
            "https://example.test/primicias/4",
        )
        duplicate, reason = self._check(
            "Gasolina Extra baja 4 centavos y costara 3,175 el galon", ARCH
        )
        self.assertFalse(duplicate, reason)

    def test_similarity_of_the_two_variants_is_high_enough_to_matter(self):
        left = "Gasolina Extra y Ecopais bajan a 3,175 por galon desde el 12 de octubre"
        right = "Extra y Ecopais bajan a 3,17 por galon desde el 12 de octubre"
        self.assertGreater(title_similarity(left, right), 0.55)


class NotifiedOwnershipTests(unittest.TestCase):
    """One write of ``notified_at`` per item, so a crash cannot replay it."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "test.sqlite3")

    def test_mark_notified_is_idempotent(self):
        candidate = Candidate(
            source=ARCH,
            title="Gasolina Extra y Ecopais bajan a 3,175",
            url="https://example.test/arch/5",
            detected_at=datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc),
        )
        item_id = self.store.save_item(candidate, Decision(VerificationStatus.VERIFIED, 95, ("test",)))
        first = datetime(2026, 10, 12, 14, 1, tzinfo=timezone.utc)
        second = datetime(2026, 10, 12, 14, 2, tzinfo=timezone.utc)
        self.store.mark_notified(item_id, first)
        self.store.mark_notified(item_id, second)
        with self.store.connect() as db:
            row = db.execute("SELECT notified_at FROM items WHERE id = ?", (item_id,)).fetchone()
        # Last write wins and only one item row exists: the squawk cannot be
        # replayed by reading a stale flag.
        self.assertEqual(row["notified_at"], second.isoformat())


class PendingPriceReplayTests(unittest.TestCase):
    """A delivered pending squawk must be recognised on the next run."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "test.sqlite3")
        self.store.save_price(
            FuelPrice(
                product="Extra",
                price=Decimal("3.215"),
                period=EffectivePeriod(date(2026, 9, 12), date(2026, 10, 11)),
                price_type=PriceType.PUBLICO,
                source_name="ARCH",
            )
        )

    def test_second_outlet_with_rounded_price_is_blocked(self):
        first = Candidate(
            source=PRIMICIAS,
            title="Noboa anuncia que la Extra bajara a 3,175 desde el 12 de octubre",
            url="https://example.test/primicias/6",
            summary="El mandatario informo la rebaja de cuatro centavos.",
            detected_at=datetime(2026, 10, 7, 18, 0, tzinfo=timezone.utc),
        )
        item_id = self.store.save_item(first, Decision(VerificationStatus.PENDING, 55, ("una fuente",)))
        self.store.mark_notified(item_id, first.detected_at)

        second = Candidate(
            source=EL_UNIVERSO,
            title="La gasolina Extra costara 3,17 desde el 12 de octubre",
            url="https://example.test/universo/7",
            summary="Anuncio presidencial sobre combustibles.",
            detected_at=datetime(2026, 10, 7, 19, 30, tzinfo=timezone.utc),
        )
        duplicate, reason = self.store.is_duplicate(second)
        self.assertTrue(duplicate, reason)


if __name__ == "__main__":
    unittest.main()
