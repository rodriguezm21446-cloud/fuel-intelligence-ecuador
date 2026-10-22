from __future__ import annotations

import base64
import hashlib
import html
import logging
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from .extractors import extract_pdf_text
from .models import Candidate, SourceSpec
from .storage import Store
from .text import canonical_url, collapse_spaces, fold


USER_AGENT = "FuelIntelligenceEcuador/0.1 (+private research monitor; respectful conditional polling)"
# Several Ecuadorian media block unknown bots on article pages, so the article
# reader presents a mainstream browser identity. Feed polling keeps the honest
# custom identifier.
ARTICLE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


@dataclass(frozen=True, slots=True)
class FetchResult:
    status: int
    url: str
    content_type: str
    body: bytes
    etag: str | None
    last_modified: str | None


class HttpClient:
    def __init__(self, timeout: int = 20):
        self.timeout = timeout

    def get(
        self, url: str, etag: str | None = None, last_modified: str | None = None,
        *, browser_identity: bool = False,
    ) -> FetchResult:
        user_agent = ARTICLE_USER_AGENT if browser_identity else USER_AGENT
        headers = {"User-Agent": user_agent, "Accept": "application/rss+xml, application/xml, text/html, application/pdf;q=0.9, */*;q=0.5"}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return FetchResult(
                    status=response.status,
                    url=response.geturl(),
                    content_type=response.headers.get_content_type(),
                    body=response.read(),
                    etag=response.headers.get("ETag"),
                    last_modified=response.headers.get("Last-Modified"),
                )
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                return FetchResult(304, url, "", b"", etag, last_modified)
            raise


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self._href: str | None = None
        self._anchor_text: list[str] = []
        self.text: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.title = ""
        self._in_title = False
        self.published_at: datetime | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip += 1
            return
        if tag == "a" and self._skip == 0:
            self._href = values.get("href")
            self._anchor_text = []
        if tag == "title":
            self._in_title = True
        if tag == "time" and values.get("datetime"):
            self.published_at = parse_datetime(values["datetime"])
        if tag == "meta":
            key = (values.get("property") or values.get("name") or "").lower()
            if key in {"article:published_time", "datepublished", "date"} and values.get("content"):
                self.published_at = parse_datetime(values["content"])

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._skip:
            self._skip -= 1
            return
        if tag == "a" and self._href is not None:
            label = collapse_spaces(" ".join(self._anchor_text))
            if label:
                self.links.append((self._href, label))
            self._href = None
            self._anchor_text = []
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        value = collapse_spaces(data)
        if not value:
            return
        self.text.append(value)
        if self._href is not None:
            self._anchor_text.append(value)
        if self._in_title:
            self.title = collapse_spaces(f"{self.title} {value}")


class TelegramChannelParser(HTMLParser):
    """Extract individual posts from Telegram's public channel preview."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.posts: list[dict] = []
        self._post: dict | None = None
        self._wrap_depth = 0
        self._text_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if "tgme_widget_message_wrap" in classes:
            self._wrap_depth = 1
            self._post = {"id": "", "text": [], "published_at": None}
        elif self._post is not None and tag == "div":
            self._wrap_depth += 1
        if self._post is None:
            return
        if values.get("data-post"):
            self._post["id"] = values["data-post"]
        if "tgme_widget_message_text" in classes:
            self._text_depth = 1
        elif self._text_depth and tag == "div":
            self._text_depth += 1
        if tag == "time" and values.get("datetime"):
            self._post["published_at"] = parse_datetime(values["datetime"])
        if tag == "br" and self._text_depth:
            self._post["text"].append(" ")

    def handle_endtag(self, tag: str) -> None:
        if self._post is None:
            return
        if tag != "div":
            return
        if self._text_depth:
            self._text_depth -= 1
        self._wrap_depth -= 1
        if self._wrap_depth == 0:
            if self._post.get("id") and collapse_spaces(" ".join(self._post["text"])):
                self.posts.append(self._post)
            self._post = None
            self._text_depth = 0

    def handle_data(self, data: str) -> None:
        if self._post is not None and self._text_depth:
            value = collapse_spaces(data)
            if value:
                self._post["text"].append(value)


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _child_text(element: ET.Element, names: set[str]) -> str:
    for child in element.iter():
        name = child.tag.rsplit("}", 1)[-1].lower()
        if name in names and child.text:
            return collapse_spaces(html.unescape(child.text))
    return ""


def _atom_link(element: ET.Element) -> str:
    for child in element.iter():
        if child.tag.rsplit("}", 1)[-1].lower() != "link":
            continue
        href = child.attrib.get("href")
        if href and child.attrib.get("rel", "alternate") in {"alternate", ""}:
            return href
        if child.text:
            return child.text.strip()
    return ""


def parse_rss(source: SourceSpec, payload: bytes, detected_at: datetime) -> list[Candidate]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        # Aggregators (for example Google News) sometimes answer a datacenter
        # with an HTML challenge or consent page instead of a feed. One broken
        # feed must never abort the whole run or look like a code failure.
        return []
    entries = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}]
    candidates: list[Candidate] = []
    for entry in entries[:50]:
        title = _child_text(entry, {"title"})
        link = _child_text(entry, {"link"}) or _atom_link(entry)
        summary = _child_text(entry, {"description", "summary", "content"})
        date_raw = _child_text(entry, {"pubdate", "published", "updated", "date"})
        if not title or not link:
            continue
        candidates.append(
            Candidate(
                source=source,
                title=title,
                url=urljoin(source.url, link),
                summary=re.sub(r"<[^>]+>", " ", summary),
                published_at=parse_datetime(date_raw),
                detected_at=detected_at,
                raw_kind="rss",
            )
        )
    return candidates


def parse_telegram_channel(source: SourceSpec, payload: bytes, detected_at: datetime) -> list[Candidate]:
    parser = TelegramChannelParser()
    parser.feed(payload.decode("utf-8", errors="replace"))
    candidates = []
    for post in parser.posts[-50:]:
        message = collapse_spaces(" ".join(post["text"]))
        candidates.append(
            Candidate(
                source=source,
                title=message[:240],
                url=f"https://t.me/{post['id']}",
                summary=message,
                published_at=post["published_at"],
                detected_at=detected_at,
                raw_kind="telegram",
            )
        )
    return candidates


def parse_html(source: SourceSpec, payload: bytes, detected_at: datetime, final_url: str) -> list[Candidate]:
    text = payload.decode("utf-8", errors="replace")
    parser = PageParser()
    parser.feed(text)
    body = collapse_spaces(" ".join(parser.text))
    if source.kind == "html":
        return [
            Candidate(
                source=source,
                title=parser.title or source.name,
                url=final_url,
                body=body,
                published_at=parser.published_at,
                detected_at=detected_at,
                raw_kind="html",
            )
        ]

    seen: set[str] = set()
    candidates: list[Candidate] = []
    for href, label in parser.links:
        url = canonical_url(urljoin(final_url, href))
        if url in seen or url.startswith(("mailto:", "javascript:")):
            continue
        seen.add(url)
        candidates.append(
            Candidate(
                source=source,
                title=label,
                url=url,
                detected_at=detected_at,
                raw_kind="link",
            )
        )
    return candidates[:100]


class SourcePoller:
    def __init__(self, client: HttpClient, store: Store):
        self.client = client
        self.store = store

    def poll(self, source: SourceSpec, detected_at: datetime) -> tuple[list[Candidate], bool]:
        state = self.store.source_state(source.id)
        is_bootstrap = not bool(state.get("content_hash"))
        try:
            result = self.client.get(
                source.url,
                etag=state.get("etag") if isinstance(state.get("etag"), str) else None,
                last_modified=state.get("last_modified") if isinstance(state.get("last_modified"), str) else None,
            )
            if result.status == 304:
                self.store.update_source_state(source.id)
                return [], is_bootstrap

            digest = hashlib.sha256(result.body).hexdigest()
            if digest == state.get("content_hash"):
                self.store.update_source_state(
                    source.id,
                    etag=result.etag,
                    last_modified=result.last_modified,
                    content_hash=digest,
                )
                return [], is_bootstrap

            content_type = result.content_type.lower()
            if source.kind == "rss" or "xml" in content_type:
                candidates = parse_rss(source, result.body, detected_at)
            elif source.kind == "telegram":
                candidates = parse_telegram_channel(source, result.body, detected_at)
            elif "pdf" in content_type or result.url.lower().endswith(".pdf"):
                document_text = extract_pdf_text(result.body)
                candidates = [
                    Candidate(
                        source=source,
                        title=source.name,
                        url=result.url,
                        body=document_text,
                        published_at=detected_at,
                        detected_at=detected_at,
                        raw_kind="pdf",
                    )
                ]
            else:
                candidates = parse_html(source, result.body, detected_at, result.url)

            for candidate in candidates:
                candidate.content_hash = hashlib.sha256(
                    f"{canonical_url(candidate.url)}\n{fold(candidate.title)}\n{fold(candidate.summary or candidate.body)}".encode("utf-8")
                ).hexdigest()
            self.store.update_source_state(
                source.id,
                etag=result.etag,
                last_modified=result.last_modified,
                content_hash=digest,
            )
            return candidates, is_bootstrap
        except Exception as exc:
            self.store.update_source_state(source.id, error=f"{type(exc).__name__}: {exc}")
            raise

    def enrich(self, candidate: Candidate) -> Candidate:
        if candidate.raw_kind not in {"link", "rss"}:
            return candidate
        target = candidate.url
        try:
            result = self.client.get(target, browser_identity=True)
        except Exception:
            self._log_enrich(candidate, 0, "fetch_failed")
            return candidate
        resolved_url = result.url
        if "news.google.com" in result.url:
            resolved = _resolve_google_news_link(candidate.url, result.body)
            if resolved:
                try:
                    result = self.client.get(resolved, browser_identity=True)
                    resolved_url = result.url
                except Exception:
                    self._log_enrich(candidate, 0, "publisher_fetch_failed")
                    return candidate
            else:
                self._log_enrich(candidate, 0, "publisher_unresolved")
                return candidate
        candidate.url = resolved_url
        if "pdf" in result.content_type or result.url.lower().endswith(".pdf"):
            candidate.body = extract_pdf_text(result.body)
            candidate.raw_kind = "pdf"
        elif "html" in result.content_type:
            parser = PageParser()
            parser.feed(result.body.decode("utf-8", errors="replace"))
            candidate.body = collapse_spaces(" ".join(parser.text))
            candidate.published_at = candidate.published_at or parser.published_at
        candidate.content_hash = hashlib.sha256(
            f"{canonical_url(candidate.url)}\n{fold(candidate.title)}\n{fold(candidate.summary or candidate.body)}".encode("utf-8")
        ).hexdigest()
        self._log_enrich(candidate, len(candidate.body or ""), "ok")
        return candidate

    @staticmethod
    def _log_enrich(candidate: Candidate, body_len: int, outcome: str) -> None:
        logging.getLogger("fuelintel").info(
            "WF_02_READER source=%s outcome=%s body_len=%d url=%s",
            candidate.source.id, outcome, body_len, canonical_url(candidate.url),
        )


def _resolve_google_news_link(url: str, page: bytes) -> str | None:
    """Resolve a news.google.com article link to its publisher URL.

    The path segment after /articles/ is a base64url protobuf blob that embeds
    the publisher URL; a direct decode avoids the JavaScript consent page.
    """
    path = urlsplit(url).path
    segment = path.rsplit("/", 1)[-1]
    if not re.match(r"^CBMi", segment):
        return None
    try:
        decoded = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)).decode("utf-8", errors="replace")
    except Exception:
        return None
    for match in re.findall(r"https?://[A-Za-z0-9._%-]+\.[A-Za-z]{2,}[^\x01-\x1f\s]*", decoded):
        if all(bad not in match for bad in ("google.", "youtube.", "news.com")):
            return match
    return None
