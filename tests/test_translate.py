import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fuelintel.models import Candidate, SourceSpec, VerificationStatus
from fuelintel.pipeline import Pipeline
from fuelintel.storage import Store
from fuelintel.translate import looks_spanish, translate_to_spanish
from tests.test_core import _CaptureNotifier, _NoEnrichment


class _SpyEditor:
    enabled = True
    model = "spy"
    last_error = ""

    def __init__(self):
        self.calls: list[str] = []

    def translate(self, text, target_language="es"):
        self.calls.append(text)
        return "Traducción al español de la noticia de combustibles"

    def review(self, evidence, fallback_x):
        return None


class _DeadEditor:
    enabled = True
    model = "dead"
    last_error = ""

    def translate(self, text, target_language="es"):
        return None

    def review(self, evidence, fallback_x):
        return None


class LanguageTests(unittest.TestCase):
    def test_spanish_sentence_is_recognised(self):
        self.assertTrue(looks_spanish("El precio de la gasolina sube desde el 12"))

    def test_english_sentence_is_not_spanish(self):
        self.assertFalse(looks_spanish("Oil prices rise after a supply cut"))

    def test_numbers_and_proper_nouns_default_to_spanish(self):
        self.assertTrue(looks_spanish("PETROECUADOR 2026"))

    def test_arabic_is_never_spanish(self):
        self.assertFalse(looks_spanish("ارتفاع أسعار النفط"))
        self.assertFalse(looks_spanish("تعطل إمدادات الخام"))

    def test_cyrillic_is_never_spanish(self):
        self.assertFalse(looks_spanish("Нефть растёт"))


class TranslateToSpanishTests(unittest.TestCase):
    def test_spanish_input_is_left_untouched(self):
        result = translate_to_spanish("La gasolina sube", _SpyEditor())
        self.assertFalse(result.translated)
        self.assertEqual(result.engine, "original")

    def test_engine_translates_foreign_text(self):
        result = translate_to_spanish("Diesel is up", _SpyEditor())
        self.assertTrue(result.translated)
        self.assertEqual(result.engine, "gemini")

    def test_arabic_is_sent_to_the_engine(self):
        result = translate_to_spanish("ارتفاع أسعار النفط بعد تعطل الإمدادات", _SpyEditor())
        self.assertTrue(result.translated)
        self.assertEqual(result.engine, "gemini")

    def test_no_engine_marks_text_untranslated(self):
        result = translate_to_spanish("Diesel is up", None)
        self.assertFalse(result.translated)
        self.assertEqual(result.engine, "none")


class PipelineTranslationTests(unittest.TestCase):
    INTL = SourceSpec(
        id="intl-wire",
        name="International Wire",
        url="https://example.test/wire",
        kind="rss",
        trust=95,
        country="INTL",
        max_age_hours=36,
    )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "test.sqlite3")
        self.notifier = _CaptureNotifier()

    def _candidate(self):
        return Candidate(
            source=self.INTL,
            title="Diesel supply tightens as refinery halts operations",
            url="https://example.test/wire/story",
            summary="The refinery stopped after a supply cut, traders said.",
            published_at=datetime(2026, 9, 8, 14, 0, tzinfo=timezone.utc),
            detected_at=datetime(2026, 9, 8, 14, 5, tzinfo=timezone.utc),
            content_hash="intl-translate",
        )

    def _pipeline(self, editor):
        return Pipeline(self.store, _NoEnrichment(), self.notifier, timezone.utc, editor=editor)

    def test_english_news_is_translated_before_delivery(self):
        editor = _SpyEditor()
        result = self._pipeline(editor).process(self._candidate())
        self.assertEqual(result.status, VerificationStatus.VERIFIED)
        self.assertTrue(editor.calls)
        joined = "\n".join(self.notifier.messages)
        self.assertIn("Traducción al español", joined)
        self.assertNotIn("refinery halts operations", joined)

    def test_spanish_news_avoids_translation(self):
        editor = _SpyEditor()
        candidate = self._candidate()
        candidate = Candidate(
            source=candidate.source,
            title="El diésel sube tras una caída de suministro",
            url=candidate.url,
            summary="La refinería se detuvo, según los comerciantes.",
            published_at=candidate.published_at,
            detected_at=candidate.detected_at,
            content_hash="ec-translate",
        )
        self._pipeline(editor).process(candidate)
        self.assertEqual(editor.calls, [])

    def test_spanish_headline_over_english_body_is_translated(self):
        editor = _SpyEditor()
        candidate = Candidate(
            source=self.INTL,
            title="El diésel sube en Ecuador",
            url="https://example.test/wire/mixed",
            summary="Diesel is up after a supply cut, traders said.",
            published_at=datetime(2026, 9, 8, 14, 0, tzinfo=timezone.utc),
            detected_at=datetime(2026, 9, 8, 14, 5, tzinfo=timezone.utc),
            content_hash="intl-mixed",
        )
        self._pipeline(editor).process(candidate)
        self.assertTrue(editor.calls)
        joined = "\n".join(self.notifier.messages)
        self.assertIn("El diésel sube en Ecuador", joined)
        self.assertIn("Traducción al español", joined)
        self.assertNotIn("supply cut", joined)

    def test_arabic_news_is_translated_not_sent_raw(self):
        editor = _SpyEditor()
        candidate = Candidate(
            source=self.INTL,
            title="ارتفاع أسعار النفط",
            url="https://example.test/wire/arabic",
            summary="تعطل إمدادات الخام بعد هجوم",
            published_at=datetime(2026, 9, 8, 14, 0, tzinfo=timezone.utc),
            detected_at=datetime(2026, 9, 8, 14, 5, tzinfo=timezone.utc),
            content_hash="intl-arabic",
        )
        result = self._pipeline(editor).process(candidate)
        self.assertEqual(result.status, VerificationStatus.VERIFIED)
        self.assertTrue(editor.calls)
        joined = "\n".join(self.notifier.messages)
        self.assertNotIn("النفط", joined)
        self.assertIn("Traducción al español", joined)

    def test_failed_translation_never_surfaces_english(self):
        self._pipeline(_DeadEditor()).process(self._candidate())
        joined = "\n".join(self.notifier.messages)
        self.assertNotIn("refinery halts operations", joined)
        self.assertNotIn("traders said", joined)
        self.assertIn("traducción no disponible", joined)


if __name__ == "__main__":
    unittest.main()
