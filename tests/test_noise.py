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
    def test_foreign_market_story_is_rejected_as_context(self):
        decision = evaluate_candidate(
            _candidate("Oil prices rise on an OPEC supply cut"), NOW, ecuador_only=True
        )
        self.assertEqual(decision.status, VerificationStatus.REJECTED)
        self.assertIn("anclaje", " ".join(decision.reasons))

    def test_foreign_story_naming_ecuador_is_kept(self):
        decision = evaluate_candidate(
            _candidate("Ecuador diesel imports rise after an OPEC cut"), NOW, ecuador_only=True
        )
        self.assertNotEqual(decision.status, VerificationStatus.REJECTED)

    def test_gate_off_keeps_the_foreign_story(self):
        decision = evaluate_candidate(
            _candidate("Oil prices rise on an OPEC supply cut"), NOW, ecuador_only=False
        )
        self.assertNotEqual(decision.status, VerificationStatus.REJECTED)

    def test_ecuador_source_is_never_gated(self):
        decision = evaluate_candidate(_candidate("Sube el diésel", EC), NOW, ecuador_only=True)
        self.assertNotEqual(decision.status, VerificationStatus.REJECTED)


if __name__ == "__main__":
    unittest.main()
