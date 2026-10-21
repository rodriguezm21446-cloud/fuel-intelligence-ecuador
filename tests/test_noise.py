from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from fuelintel.filters import evaluate_candidate, has_ecuador_anchor
from fuelintel.models import Candidate, SourceSpec, VerificationStatus


NOW = datetime(2026, 9, 8, 15, 0, tzinfo=timezone.utc)

INTL = SourceSpec(
    id="intl-wire",
    name="International Wire",
    url="https://example.test/wire",
    kind="rss",
    trust=75,
    country="INTL",
    max_age_hours=36,
)
EC = SourceSpec(
    id="ec-prensa",
    name="Ecuador Press",
    url="https://example.test/ec",
    kind="rss",
    trust=75,
    country="EC",
    max_age_hours=36,
)


def _candidate(title: str, source: SourceSpec = INTL) -> Candidate:
    return Candidate(
        source=source,
        title=title,
        url="https://example.test/a",
        published_at=NOW - timedelta(hours=1),
        detected_at=NOW - timedelta(minutes=50),
        content_hash=title,
    )


class AnchorTests(unittest.TestCase):
    def test_anchor_detects_ecuador_and_its_assets(self):
        self.assertTrue(has_ecuador_anchor(_candidate("Petroecuador announces a diesel plan")))
        self.assertTrue(has_ecuador_anchor(_candidate("La refinería de Esmeraldas reanuda")))
        self.assertFalse(has_ecuador_anchor(_candidate("OPEC keeps its output steady")))


class NoiseGateTests(unittest.TestCase):
    def test_common_foreign_market_story_is_rejected_as_context(self):
        decision = evaluate_candidate(
            _candidate("Oil prices edge higher on strong demand"), NOW, ecuador_only=True
        )
        self.assertEqual(decision.status, VerificationStatus.REJECTED)
        self.assertIn("anclaje", " ".join(decision.reasons))

    def test_high_impact_foreign_story_is_kept(self):
        decision = evaluate_candidate(
            _candidate("Tanker seized near the Strait of Hormuz"), NOW, ecuador_only=True
        )
        self.assertNotEqual(decision.status, VerificationStatus.REJECTED)

    def test_foreign_story_naming_ecuador_is_kept(self):
        decision = evaluate_candidate(
            _candidate("Ecuador diesel imports rise after an OPEC cut"), NOW, ecuador_only=True
        )
        self.assertNotEqual(decision.status, VerificationStatus.REJECTED)

    def test_gate_off_keeps_the_foreign_story(self):
        decision = evaluate_candidate(
            _candidate("Oil prices edge higher on strong demand"), NOW, ecuador_only=False
        )
        self.assertNotEqual(decision.status, VerificationStatus.REJECTED)

    def test_ecuador_source_is_never_gated(self):
        decision = evaluate_candidate(_candidate("Sube el diésel", EC), NOW, ecuador_only=True)
        self.assertNotEqual(decision.status, VerificationStatus.REJECTED)


class HeadlineGateTests(unittest.TestCase):
    def test_domestic_story_without_fuel_headline_never_reaches_the_operator(self):
        decision = evaluate_candidate(
            _candidate("Presidente inaugura hospital en Quito", EC), NOW, ecuador_only=True
        )
        self.assertEqual(decision.status, VerificationStatus.REJECTED)

    def test_relevant_domestic_story_without_fuel_headline_uses_the_title_gate(self):
        decision = evaluate_candidate(
            _candidate("Importación de fertilizantes crece en Ecuador", EC), NOW
        )
        self.assertEqual(decision.status, VerificationStatus.REJECTED)
        self.assertIn("titular no es del dominio de combustibles", " ".join(decision.reasons))

    def test_incidental_fuel_word_in_body_is_not_enough(self):
        decision = evaluate_candidate(
            _candidate("Cambian taxistas y rutas del transporte urbano en Quito", EC), NOW
        )
        self.assertEqual(decision.status, VerificationStatus.REJECTED)

    def test_official_fuel_source_exceeds_the_headline_gate(self):
        official = SourceSpec(
            id="arch-precios", name="ARCH", url="https://example.test/arch",
            kind="html", trust=100, country="EC", max_age_hours=24,
        )
        candidate = Candidate(
            source=official,
            title="Comunicado número 12",
            url="https://example.test/c",
            published_at=NOW - timedelta(hours=1),
            detected_at=NOW - timedelta(minutes=50),
            content_hash="doc-12",
        )
        decision = evaluate_candidate(candidate, NOW)
        self.assertNotIn("titular no es del dominio de combustibles", " ".join(decision.reasons))


if __name__ == "__main__":
    unittest.main()
