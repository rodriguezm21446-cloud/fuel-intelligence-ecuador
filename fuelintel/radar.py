from __future__ import annotations

"""Radar de fuentes que bloquean a Cloudflare.

ARCH (HTTP 403 persistente) y los agregadores de Google News en Ecuador
(HTTP 503 recurrente) rechazan las IP de Cloudflare, pero aceptan lecturas
desde las IP de GitHub Actions. Este módulo corre en Actions con cadencia
corta y alimenta el pipeline directamente, sin pasar por el Worker. La
semántica es idéntica al daemon local: bootstrap sin notificación, dedupe
por store y validación completa antes de cualquier envío.
"""

import logging
from datetime import datetime, timezone

from .config import Settings, load_sources
from .main import build_runtime, poll_source

LOG = logging.getLogger("fuelintel")

RADAR_SOURCE_IDS = {
    "arch-precios",
    "arch-biblioteca",
    "google-news-ecuador-combustibles",
    "google-news-ecuador-precios",
    "google-news-ecuador-presidente",
    "lahora-economia",
    "eltelegrafo-economia",
}


def run(settings: Settings) -> int:
    store, poller, _, pipeline = build_runtime(settings)
    sources = [source for source in load_sources(settings.sources_file) if source.id in RADAR_SOURCE_IDS]
    if not sources:
        LOG.warning("el radar no encontró fuentes que vigilar; verifique config/sources.json")
        return 2
    analyzed = 0
    failures = 0
    now = datetime.now(timezone.utc)
    for source in sources:
        try:
            processed, _ = poll_source(source, poller, pipeline, now)
            analyzed += processed
        except Exception:
            failures += 1
            LOG.exception("el radar falló en la fuente %s; continúa con las demás", source.id)
    LOG.info("radar procesados=%d fallos=%d", analyzed, failures)
    return 1 if failures == len(sources) else 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Radar de fuentes bloqueadas a Cloudflare")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return run(Settings.from_env())


if __name__ == "__main__":
    import sys

    sys.exit(main())
