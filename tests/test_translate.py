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
    allow_public_translation = False

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
    allow_public_translation = False

    def translate(self, text, target_language="es"):
        return None

    def review(self, evidence, fallback_x):
        return None


class _InventingEditor:
    enabled = True
    model = "inventing"
    last_error = ""
    allow_public_translation = False

    def translate(self, text, target_language="es"):
        return None

    def review(self, evidence, fallback_x):
        from fuelintel.ai import Editorial
        return Editorial(
            analysis="El precio subió mucho.",
            x_text="Subió mucho.",
            doubts="",
            summary="El precio subió a $9.99 el 12 de octubre.",
        )


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
        result = translate_to_spanish("Diesel is up", None, allow_public=False)
        self.assertFalse(result.translated)
        self.assertEqual(result.engine, "none")
        # The original wording is preserved so the caller can quote it and the
        # operator can still recognise the story.
        self.assertEqual(result.text, "Diesel is up")
        self.assertEqual(result.original, "Diesel is up")

    def test_public_endpoint_is_used_only_when_no_editor(self):
        class _Dead:
            enabled = True

            def translate(self, text, target_language="es"):
                return None

        # A dead editor must not cost the extra network hop when nothing else
        # is configured: the public lane still gets a chance.
        result = translate_to_spanish("Diesel is up", _Dead(), allow_public=False)
        self.assertEqual(result.engine, "none")


class LanguageDetectionTests(unittest.TestCase):
    """Short English headlines used to be classified as Spanish and sent raw."""

    def test_short_english_headline_is_not_spanish(self):
        self.assertFalse(looks_spanish("Oil prices rise after a supply cut"))

    def test_english_headline_with_spanish_looking_function_words(self):
        # "diesel" was in the Spanish hint set while also being an English
        # word, so it scored 1-0 against English and the tie was resolved as
        # Spanish.  Shared spellings must not count as Spanish evidence.
        self.assertFalse(looks_spanish("The diesel of the refinery is down"))

    def test_romanian_headline_is_not_spanish(self):
        self.assertFalse(looks_spanish("Prețul motorinei scade pentru șoferi"))

    def test_long_spanish_sentence_is_still_spanish(self):
        self.assertTrue(looks_spanish("Las gasolinas Extra y Ecopais bajan de precio"))

    def test_turkish_headline_is_not_spanish(self):
        self.assertFalse(looks_spanish("Benzin fiyatları düşüyor"))

    def test_vietnamese_headline_is_not_spanish(self):
        self.assertFalse(looks_spanish("Giá xăng dầu giảm mạnh"))

    def test_spanish_domain_words_settle_a_short_headline(self):
        self.assertTrue(looks_spanish("Gasolina Extra baja a 3,175"))

    def test_tickers_only_default_to_spanish(self):
        self.assertTrue(looks_spanish("WTI 68.40 RBOB 2.05"))


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
        """A Spanish headline must not hide an untranslated English body."""
        editor = _SpyEditor()
        candidate = Candidate(
            source=self.INTL,
            title="El diésel sube en Ecuador tras un recorte de suministro",
            url="https://example.test/wire/mixed",
            summary="Diesel is up after a supply cut, traders said.",
            published_at=datetime(2026, 9, 8, 14, 0, tzinfo=timezone.utc),
            detected_at=datetime(2026, 9, 8, 14, 5, tzinfo=timezone.utc),
            content_hash="intl-mixed",
        )
        result = self._pipeline(editor).process(candidate)
        self.assertTrue(editor.calls, result.reason)
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

    def test_failed_translation_labels_the_gap_instead_of_hiding_it(self):
        """An untranslated story must be labelled, not silently replaced.

        The old behaviour swapped the headline for a generic notice, so the
        operator could not tell which source had broken and the alert looked
        like a translation error with no content.
        """
        self._pipeline(_DeadEditor()).process(self._candidate())
        joined = "\n".join(self.notifier.messages)
        self.assertIn("IDIOMA · sin traducir", joined)
        self.assertIn("TRADUCCIÓN NO DISPONIBLE", joined)
        # The original headline is quoted so the story stays identifiable.
        self.assertIn("«Diesel supply tightens", joined)
        # But the foreign body prose is never delivered as the flash.
        self.assertNotIn("traders said", joined)

    def test_invented_summary_replaced_by_extractive(self):
        editor = _InventingEditor()
        candidate = Candidate(
            source=self.INTL,
            title="El precio del diésel sube en Ecuador",
            url="https://example.test/wire/inventing",
            summary="El precio del diésel es de $3.21 desde el 12 de octubre, según fuentes del sector.",
            published_at=datetime(2026, 9, 8, 14, 0, tzinfo=timezone.utc),
            detected_at=datetime(2026, 9, 8, 14, 5, tzinfo=timezone.utc),
            content_hash="intl-inventing",
        )
        result = self._pipeline(editor).process(candidate)
        self.assertEqual(result.status, VerificationStatus.VERIFIED)
        joined = "\n".join(self.notifier.messages)
        # Invented number must not surface
        self.assertNotIn("$9.99", joined)
        # Extractive fallback from source text should appear
        self.assertIn("$3.21", joined)


if __name__ == "__main__":
    unittest.main()
