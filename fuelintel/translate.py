"""Hybrid translation: the editorial engine first, a local engine as backup.

Nothing that reaches the operator may stay in English. The preferred engine is
Gemini (already configured for editing); if it is unavailable or drops a figure
the module falls back to a local OSS engine when installed, and otherwise marks
the text as untranslated so the pipeline can decide instead of hiding the gap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .text import collapse_spaces

_SPANISH_HINTS = {
    "el", "la", "los", "las", "de", "del", "que", "por", "para", "con", "una",
    "un", "se", "su", "no", "al", "es", "en", "y", "como", "mas", "precio",
    "gasolina", "combustible", "gobierno", "presidente", "ministro", "petroleo",
    "diesel", "crudo", "sube", "baja", "aumento", "rebaja", "subsidio",
}

_ENGLISH_HINTS = {
    "the", "and", "for", "with", "from", "that", "this", "will", "oil", "price",
    "fuel", "government", "president", "minister", "crude", "gasoline", "diesel",
    "report", "said", "after", "over", "into", "supply", "market", "up", "down",
}

_TOKEN_RE = re.compile(r"[a-záéíóúüñ]+")


@dataclass(frozen=True, slots=True)
class TranslationResult:
    text: str
    engine: str = "original"
    translated: bool = False


def looks_spanish(text: str) -> bool:
    """Cheap language check: accented characters settle it, else count hint words."""
    text = text or ""
    if re.search(r"[áéíóúñü¿¡]", text, re.IGNORECASE):
        return True
    tokens = set(_TOKEN_RE.findall(text.lower()))
    if not tokens:
        return True
    spanish = len(tokens & _SPANISH_HINTS)
    english = len(tokens & _ENGLISH_HINTS)
    if spanish == 0 and english == 0:
        return True
    return spanish >= english


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
    source_language: str = "en",
) -> TranslationResult:
    """Return Spanish text for user-facing copy, preferring Gemini, then local."""
    clean = collapse_spaces(text or "")
    if not clean or looks_spanish(clean):
        return TranslationResult(clean, "original", False)
    if editor is not None and getattr(editor, "enabled", False):
        translated = editor.translate(clean, target_language)
        if translated:
            return TranslationResult(collapse_spaces(translated), "gemini", True)
    offline = _offline_translate(clean, target_language, source_language)
    if offline:
        return TranslationResult(offline, "offline", True)
    return TranslationResult(clean, "none", False)
