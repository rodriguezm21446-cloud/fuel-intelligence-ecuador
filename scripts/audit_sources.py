"""Read-only source audit for operators. It never sends Telegram messages."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from fuelintel.config import load_sources
from fuelintel.models import SourceSpec
from fuelintel.sources import HttpClient, parse_html, parse_rss, parse_telegram_channel


def check(source: SourceSpec) -> tuple[str, str, int, int, str]:
    try:
        result = HttpClient(timeout=20).get(source.url)
        content_type = result.content_type
        if source.kind == "rss" or "xml" in content_type:
            candidates = parse_rss(source, result.body, datetime.now(timezone.utc))
        elif source.kind == "telegram":
            candidates = parse_telegram_channel(source, result.body, datetime.now(timezone.utc))
        else:
            candidates = parse_html(source, result.body, datetime.now(timezone.utc), result.url)
        status = "OK" if candidates else "PARSER_EMPTY"
        return source.id, status, result.status, len(candidates), result.url
    except Exception as error:
        return source.id, "ERROR", 0, 0, f"{type(error).__name__}: {error}"[:180]


def main() -> int:
    sources = load_sources(Path("config/sources.json"))
    failures = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(check, source): source.id for source in sources}
        for future in as_completed(futures):
            source_id, state, http_status, candidates, detail = future.result()
            print(f"{source_id}\t{state}\thttp={http_status}\tcandidates={candidates}\t{detail}", flush=True)
            failures += state != "OK"
    print(f"TOTAL={len(sources)} FAILURES={failures}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
