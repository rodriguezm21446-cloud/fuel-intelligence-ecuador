"""Process one event selected and deduplicated by the Cloudflare detector."""

from __future__ import annotations

import hashlib
import os
import sys
from datetime import datetime, timezone

from .config import Settings, load_sources
from .main import build_runtime
from .models import Candidate
from .sources import parse_datetime
from .text import canonical_url, fold


def required(name: str, maximum: int) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"falta la entrada {name}")
    if len(value) > maximum:
        raise ValueError(f"{name} supera el tamaño permitido")
    return value


def main() -> int:
    settings = Settings.from_env()
    sources = {source.id: source for source in load_sources(settings.sources_file)}
    source_id = required("FUELINTEL_EVENT_SOURCE_ID", 100)
    if source_id not in sources:
        raise ValueError(f"fuente desconocida: {source_id}")

    event_url = required("FUELINTEL_EVENT_URL", 2000)
    if not event_url.startswith(("https://", "http://")):
        raise ValueError("la URL del evento no es HTTP(S)")
    title = required("FUELINTEL_EVENT_TITLE", 500)
    detected_at = parse_datetime(os.getenv("FUELINTEL_EVENT_DETECTED_AT")) or datetime.now(timezone.utc)
    corroborated_by = os.getenv("FUELINTEL_EVENT_CORROBORATED_BY", "").strip()[:200]

    _, poller, _, pipeline = build_runtime(settings)
    candidate = Candidate(
        source=sources[source_id],
        title=title,
        url=canonical_url(event_url),
        detected_at=detected_at,
        raw_kind="link",
    )
    candidate = poller.enrich(candidate)
    if not candidate.content_hash:
        candidate.content_hash = hashlib.sha256(
            f"{canonical_url(candidate.url)}\n{fold(candidate.title)}\n{fold(candidate.summary or candidate.body)}".encode("utf-8")
        ).hexdigest()
    result = pipeline.process(candidate, allow_notify=True, corroborated_by=corroborated_by)
    print(f"status={result.status} notified={result.notified} reason={result.reason}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"cloud_job_error={type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1)
