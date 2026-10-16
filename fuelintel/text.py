from __future__ import annotations

import hashlib
import re
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
}


def collapse_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def tidy_lines(value: str) -> str:
    """Collapse runs of spaces inside each line while keeping paragraph breaks."""
    lines = (re.sub(r"[ \t]+", " ", line).strip() for line in (value or "").replace("\r", "").split("\n"))
    return "\n".join(line for line in lines if line)


def fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", collapse_spaces(value).lower())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def canonical_url(value: str) -> str:
    parts = urlsplit(value.strip())
    query = []
    for key, item in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower().startswith("utm_") or key.lower() in TRACKING_QUERY_KEYS:
            continue
        query.append((key, item))
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path.rstrip("/") or "/", urlencode(query), ""))


def content_fingerprint(*parts: str) -> str:
    normalized = "\n".join(fold(part) for part in parts if part)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def title_tokens(value: str) -> set[str]:
    stop = {
        "para", "como", "desde", "hasta", "sobre", "entre", "ante", "tras",
        "del", "las", "los", "una", "unos", "unas", "por", "con", "sin",
        "que", "ecuador", "noticia", "ultima", "este", "esta", "son", "sus",
    }
    return {token for token in re.findall(r"[a-z0-9]+", fold(value)) if len(token) >= 3 and token not in stop}


def title_similarity(left: str, right: str) -> float:
    a, b = title_tokens(left), title_tokens(right)
    if not a or not b:
        return 0.0
    token_score = len(a & b) / len(a | b)
    sequence_score = SequenceMatcher(None, fold(left), fold(right)).ratio()
    return max(token_score, sequence_score)


def trim_x(text: str, limit: int = 280) -> str:
    value = collapse_spaces(text)
    if len(value) <= limit:
        return value
    shortened = value[: max(0, limit - 1)].rsplit(" ", 1)[0].rstrip(" ,.;:")
    return shortened + "…"
