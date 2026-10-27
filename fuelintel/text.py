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


def normalized_numbers(value: str) -> tuple[str, ...]:
    """Comparable key for the figures in a headline.

    Two things went wrong with the old raw comparison:

    * ``3.175`` and ``3,175`` are the same amount written with a different
      decimal separator, so the separator is normalised.
    * ``3,17`` and ``3,175`` are *not* the same price, but they are the same
      price to two decimals, and the duplicate gate used to skip the whole
      title check whenever the figure lists differed at all.

    Returns an order-independent tuple of normalised decimals.  Trailing zeros
    are stripped (3,1750 -> 3.175) but two- versus three-decimal precision is
    preserved, so the caller can tell a rounding difference from a new price.
    """
    keys: set[str] = set()
    for token in re.findall(r"\d+(?:[.,]\d+)?", value or ""):
        raw = token.replace(",", ".")
        whole, _, fraction = raw.partition(".")
        fraction = fraction.rstrip("0")
        keys.add(f"{whole}.{fraction}" if fraction else whole)
    return tuple(sorted(keys))


def _same_amount(left: str, right: str) -> bool:
    """True when two figures differ only in precision (3,175 vs 3,17).

    Only the *price* of a headline may be rounded by an outlet.  Dates, days
    and counts must match exactly: 12 de octubre and 12 de noviembre are not
    the same announcement, and neither are 3,21 and 3,175.
    """
    left_whole, _, left_fraction = left.partition(".")
    right_whole, _, right_fraction = right.partition(".")
    if left_whole != right_whole:
        return False
    if not left_fraction or not right_fraction:
        return left_fraction == right_fraction
    shared = min(len(left_fraction), len(right_fraction))
    if left_fraction[:shared] != right_fraction[:shared]:
        return False
    # A rounding difference is at most one extra significant digit.
    return abs(len(left_fraction) - len(right_fraction)) <= 1


def same_announcement(left: str, right: str, similarity_threshold: float = 0.78) -> bool:
    """Decide whether two headlines report the same price announcement.

    The previous gate bailed out with ``continue`` as soon as the two figure
    lists differed as raw strings, so a headline saying ``3,175`` was never
    compared against one saying ``3,17`` and the same squawk went out twice.

    The figure lists now only *veto* a match when they really disagree: a
    different amount (3,21 vs 3,175) means a different announcement, a rounding
    difference (3,17 vs 3,175) does not.  Wording is still compared, and two
    stories that share the same figures need a lower similarity to match
    because outlets rarely phrase the same announcement the same way.
    """
    score = title_similarity(left, right)
    left_numbers = normalized_numbers(left)
    right_numbers = normalized_numbers(right)
    identical_figures = bool(left_numbers) and left_numbers == right_numbers
    if left_numbers and right_numbers and not identical_figures:
        # Different figures: only a rounded price (3,175 vs 3,17) may still be
        # the same story, and even then the wording must be near-identical.
        rounded_price_only = (
            len(left_numbers) == len(right_numbers)
            and all(_same_amount(a, b) for a, b in zip(left_numbers, right_numbers))
            and any("." in a or "." in b for a, b in zip(left_numbers, right_numbers))
        )
        if not rounded_price_only:
            return False
        return score >= 0.8
    if score >= similarity_threshold:
        return True
    # Same figures, different outlet wording: still one announcement, but the
    # wording must be recognisably the same story.  A lower bar than the
    # headline threshold, high enough that "Extra a 3,175" and "Diésel a 3,175"
    # stay separate products.
    return identical_figures and score >= 0.62


def title_similarity(left: str, right: str) -> float:
    a, b = title_tokens(left), title_tokens(right)
    if not a or not b:
        return 0.0
    token_score = len(a & b) / len(a | b)
    sequence_score = SequenceMatcher(None, fold(left), fold(right)).ratio()
    # A containment ratio catches the common outlet pattern where one headline
    # is a subset of the other ("Extra baja a 3,175" inside "Nuevo precio de la
    # gasolina Extra: 3,175 por galón"); plain Jaccard punishes the extra words.
    containment = len(a & b) / min(len(a), len(b))
    return max(token_score, sequence_score, containment)


def trim_x(text: str, limit: int = 280) -> str:
    value = collapse_spaces(text)
    if len(value) <= limit:
        return value
    shortened = value[: max(0, limit - 1)].rsplit(" ", 1)[0].rstrip(" ,.;:")
    return shortened + "…"
