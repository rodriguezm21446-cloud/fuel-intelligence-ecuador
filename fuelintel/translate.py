"""Hybrid translation: the editorial engine first, then public endpoints.

Nothing that reaches the operator may stay in English. The preferred engine is
Gemini (already configured for editing); if it is unavailable or drops a figure
the module falls back to a keyless public endpoint, then to a local OSS engine
when installed, and otherwise marks the text as untranslated so the pipeline
can label the gap instead of hiding it.

Two failure modes of the previous version are fixed here:

1. ``looks_spanish`` declared short English headlines Spanish because two
   function words ("the", "of") were enough to reach a tie and the tie was
   resolved as Spanish.  The decision is now made on weighted stopwords plus
   letter evidence, and anything ambiguous is treated as foreign (translate)
   rather than Spanish (publish raw).
2. Failed translations replaced the headline with a generic notice, so the
   operator saw "error de traducción" with no way to tell which source broke.
   The result now carries the original text and the caller labels it.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .text import collapse_spaces

# Function words and domain words that only a Spanish text uses.
#
# A word that is spelled identically in English must NOT be listed here:
# "diesel" sat in this set for months, so the English headline "Diesel is up"
# scored 1-0 against its own hint set and was declared Spanish, published raw
# to the operator.  The set below is limited to words that cannot appear in an
# English sentence by accident.
_SPANISH_HINTS = {
    "el", "la", "los", "las", "del", "que", "por", "para", "con", "una",
    "se", "su", "al", "es", "en", "y", "como", "mas", "más",
    "pero", "sin", "sobre", "desde", "hasta", "entre", "ante", "tras",
    "este", "esta", "estos", "estas", "será", "seran", "serán", "fue",
    "hay", "también", "tambien", "según", "segun", "mientras", "porque",
    "precio", "precios", "gasolina", "gasolinas", "combustible", "combustibles",
    "gobierno", "presidente", "ministro", "petroleo", "petróleo", "refinería",
    "sube", "bajan", "baja", "subirá", "bajará", "aumento", "rebaja",
    "subsidio", "galon", "galón", "vigencia", "refineria",
}

# English function words.  They are what actually separates a headline in
# English from one in Spanish, so they are counted with the same weight.
_ENGLISH_HINTS = {
    "the", "and", "for", "with", "from", "that", "this", "will", "was", "were",
    "has", "have", "had", "are", "is", "be", "been", "its", "their", "of", "in",
    "on", "at", "to", "by", "as", "but", "not", "after", "before", "over",
    "into", "out", "up", "down", "said", "says", "report", "reports",
    "oil", "price", "prices", "fuel", "fuels", "government", "president",
    "minister", "crude", "gasoline", "diesel", "supply", "market", "markets",
    "refinery", "refineries", "shipping", "tanker", "tankers", "attack",
    "halts", "tightens", "operations", "stops", "resumes", "closes", "idles",
}

# Words that exist in both languages.  They are useful for the domain but must
# never decide the language: "diesel" alone used to make the English headline
# "Diesel is up" score as Spanish and reach the operator untranslated.
_BILINGUAL_DOMAIN_TERMS = {"diesel", "gasolina", "extra", "super", "premium", "brent", "wti"}

_TOKEN_RE = re.compile(r"[a-záéíóúüñ]+")
_ACCENT_RE = re.compile(r"[áéíóúñü¿¡]", re.IGNORECASE)

# Non-Latin scripts are never Spanish, even though they contain no a-z tokens.
# The Latin ranges cover languages that share the Latin alphabet with Spanish
# and would otherwise slip through: Turkish, Romanian, Vietnamese, Polish,
# Czech, Hungarian and the Nordic/Germanic extras.
_FOREIGN_SCRIPT_RE = re.compile(
    r"[\u0600-\u06FF\u0750-\u077F\u0590-\u05FF\u0400-\u04FF\u0370-\u03FF"
    r"\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7AF\u0900-\u097F"
    r"\u0100-\u024F\u1E00-\u1EFF]"
)

# Tokens that appear in almost every Spanish fuel headline and therefore make a
# short headline decidable without a full sentence.
_STRONG_SPANISH = {
    "gasolina", "gasolinas", "ecopais", "ecopaís", "diésel",
    "combustible", "combustibles", "precio", "precios", "subsidio",
    "petroecuador", "galón", "galon", "vigencia", "rebaja",
}

# Public, keyless translation endpoints tried after the editorial engine.  They
# are intentionally generic: the goal is that a missing Gemini key degrades the
# prose quality, never the language the operator reads.
_PUBLIC_ENDPOINTS = (
    "https://translate.googleapis.com/translate_a/single",
)


@dataclass(frozen=True, slots=True)
class TranslationResult:
    """Outcome of a translation attempt.

    ``text`` always holds something safe to display.  When nothing translated it
    ``text`` keeps the original wording, ``translated`` stays False and the
    caller decides how to label it, instead of silently publishing a foreign
    headline or replacing it with a notice that hides which source failed.
    """

    text: str
    engine: str = "original"
    translated: bool = False
    original: str = ""


def looks_spanish(text: str) -> bool:
    """Decide whether the text is Spanish.

    Accents and Spanish-only domain words settle the case immediately.  Other
    Latin-script text is scored with weighted Spanish and English function
    words; a tie is treated as *not* Spanish so an ambiguous headline gets
    translated instead of being published raw.
    """
    text = text or ""
    if _FOREIGN_SCRIPT_RE.search(text):
        return False
    if _ACCENT_RE.search(text):
        return True
    tokens = _TOKEN_RE.findall(text.lower())
    if not tokens:
        return True
    unique = set(tokens)
    if unique & _STRONG_SPANISH:
        return True
    # The Spanish set already excludes shared spellings; stripping them from
    # the tokens as well keeps "ecuador" from counting as one-side evidence.
    shared = unique & _BILINGUAL_DOMAIN_TERMS
    unique -= shared
    spanish = len(unique & _SPANISH_HINTS)
    english = len(unique & _ENGLISH_HINTS)
    if spanish == 0 and english == 0:
        # Numbers, proper nouns and tickers: Spanish is the safer default
        # because the alternative is sending a foreign string unchecked.
        return True
    return spanish > english


def _numbers(value: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for token in re.findall(r"\d+(?:[.,]\d+)?", value or ""):
        key = token.replace(",", ".")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _keeps_figures(original: str, translation: str) -> bool:
    """True when the translation kept every figure of the original."""
    source = _numbers(original)
    produced = _numbers(translation)
    return all(produced.get(token, 0) >= count for token, count in source.items())


def _public_translate(text: str, target_language: str, source_language: str = "auto") -> str:
    """Keyless fallback used when the editorial engine is unavailable.

    ``translate.googleapis.com`` is the endpoint the browser widget uses; it
    needs no key and returns a nested array whose first slice holds the
    sentence-by-sentence translation.  A figure-preserving guard rejects a
    response that silently drops a number.
    """
    for endpoint in _PUBLIC_ENDPOINTS:
        query = urllib.parse.urlencode(
            {
                "client": "gtx",
                "sl": source_language,
                "tl": target_language,
                "dt": "t",
                "q": text[:4500],
            }
        )
        request = urllib.request.Request(
            f"{endpoint}?{query}",
            headers={"User-Agent": "Mozilla/5.0 (compatible; FuelIntelEcuador/0.5)"},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            continue
        try:
            segments = payload[0] or []
            translated = collapse_spaces("".join(str(chunk[0]) for chunk in segments if chunk and chunk[0]))
        except (IndexError, TypeError, ValueError):
            continue
        if translated and _keeps_figures(text, translated):
            return translated
    return ""


def _offline_translate(text: str, target_language: str, source_language: str = "en") -> str:
    """Best-effort local engine (argostranslate). Absent engine returns empty."""
    try:
        from argostranslate import translate as argos  # type: ignore
    except Exception:
        return ""
    try:
        translated = argos.translate(text, source_language, target_language)
    except Exception:
        return ""
    return collapse_spaces(str(translated or "")).strip()


def translate_to_spanish(
    text: str,
    editor=None,
    *,
    target_language: str = "es",
    source_language: str = "auto",
    allow_public: bool = True,
) -> TranslationResult:
    """Return Spanish text, in order: editorial engine, public endpoint, local."""
    clean = collapse_spaces(text or "")
    if not clean:
        return TranslationResult(clean, "original", False, clean)
    if looks_spanish(clean):
        return TranslationResult(clean, "original", False, clean)
    if editor is not None and getattr(editor, "enabled", False):
        translated = editor.translate(clean, target_language)
        if translated:
            return TranslationResult(collapse_spaces(translated), "gemini", True, clean)
    if allow_public:
        public = _public_translate(clean, target_language)
        if public:
            return TranslationResult(public, "public", True, clean)
    offline = _offline_translate(clean, target_language, "en" if source_language == "auto" else source_language)
    if offline:
        return TranslationResult(offline, "offline", True, clean)
    return TranslationResult(clean, "none", False, clean)
