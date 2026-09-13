import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fuelintel.models import Candidate, SourceSpec, VerificationStatus
from fuelintel.pipeline import Pipeline
from fuelintel.storage import Store
from tests.test_core import _CaptureNotifier, _NoEnrichment


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "test.sqlite3")
        self.notifier = _CaptureNotifier()
        self.source = SourceSpec("lahora-economia", "La Hora", "https://example.test", "html_links", 72)
        self.now = datetime(2026, 9, 13, 18, tzinfo=timezone.utc)

    def candidate(self, body, **kwargs):
        return Candidate(source=self.source, title="Precios de gasolina Extra y diésel bajan en Ecuador",
                         url="https://example.test/precios", body=body, detected_at=self.now,
                         content_hash="new-report", **kwargs)

    def pipeline(self, chart_generator=None):
        return Pipeline(self.store, _NoEnrichment(), self.notifier, timezone.utc, chart_generator=chart_generator)

    def test_expired_media_prices_never_return_to_pending(self):
        result = self.pipeline().process(self.candidate(
            "Precios del 12 de agosto al 11 de septiembre de 2026. Extra $3.24 Diésel $3.18",
            published_at=self.now))
        self.assertEqual(result.status, VerificationStatus.STALE)
        self.assertFalse(result.notified)
        self.assertEqual(self.store.recent_prices(), [])

    def test_old_news_without_prices_never_sends_early_squawk(self):
        result = self.pipeline().process(self.candidate("Gasolina y petróleo bajan por oferta de combustible",
            raw_kind="rss", published_at=datetime(2026, 8, 1, tzinfo=timezone.utc)))
        self.assertEqual(result.status, VerificationStatus.STALE)
        self.assertEqual(self.notifier.messages, [])

    def test_media_at_72_reaches_operator_and_chart_failure_does_not_hide_text(self):
        with patch("fuelintel.pipeline.generate_reported_price_chart", side_effect=RuntimeError("renderer unavailable")):
            result = self.pipeline(chart_generator=lambda *_: None).process(self.candidate(
                "Precios del 12 de septiembre al 11 de octubre de 2026. Extra $3.21 Diésel $3.15",
                published_at=self.now))
        self.assertTrue(result.notified)
        self.assertEqual(result.status, VerificationStatus.PENDING)
        self.assertIn("12/09/2026", self.notifier.messages[0])

    def test_official_photo_failure_does_not_suppress_news(self):
        self.source = replace(self.source, id="arch-precios", name="ARCH", trust=100)
        with patch.object(self.notifier, "send_photo", side_effect=RuntimeError("Telegram photo failure")):
            result = self.pipeline(chart_generator=lambda *_: Path("test.png")).process(self.candidate(
                "Precios del 12 de septiembre al 11 de octubre de 2026. Extra $3.21", published_at=self.now))
        self.assertTrue(result.notified)
        self.assertEqual(len(self.notifier.messages), 1)

    def test_failed_text_can_be_retried_without_losing_item_id(self):
        candidate = self.candidate("Precios del 12 de septiembre al 11 de octubre de 2026. Extra $3.21", published_at=self.now)
        with patch.object(self.notifier, "send_text", side_effect=RuntimeError("temporary failure")):
            with self.assertRaises(RuntimeError):
                self.pipeline().process(candidate)
        result = self.pipeline().process(candidate)
        self.assertTrue(result.notified)
        self.assertGreater(result.item_id, 0)
        repeated = self.pipeline().process(candidate)
        self.assertEqual(repeated.status, VerificationStatus.DUPLICATE)


if __name__ == "__main__":
    unittest.main()
