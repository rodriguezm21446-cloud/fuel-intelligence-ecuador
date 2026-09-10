from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from .ai import GeminiEditor
from .charts import generate_dashboard
from .openbb_chart import generate_openbb_market_chart
from .config import Settings, load_sources
from .pipeline import Pipeline
from .sources import HttpClient, SourcePoller
from .storage import Store
from .supabase import SupabaseMirror
from .telegram import TelegramNotifier


LOG = logging.getLogger("fuelintel")


def build_runtime(settings: Settings):
    settings.validate_activation()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(settings.data_dir / "fuelintel.sqlite3")
    client = HttpClient(settings.request_timeout_seconds)
    poller = SourcePoller(client, store)
    notifier = TelegramNotifier(
        settings.telegram_token,
        settings.telegram_chat_id,
        settings.telegram_enabled,
        settings.dry_run,
    )
    editor = GeminiEditor(settings.gemini_api_key, settings.gemini_model, settings.request_timeout_seconds)
    pipeline = Pipeline(store, poller, notifier, settings.timezone, editor=editor)
    return store, poller, notifier, pipeline


def poll_source(source, poller: SourcePoller, pipeline: Pipeline, now: datetime) -> tuple[int, int]:
    candidates, bootstrap = poller.poll(source, now)
    processed = 0
    notified = 0
    for candidate in candidates:
        result = pipeline.process(candidate, allow_notify=not bootstrap)
        processed += 1
        notified += int(result.notified)
    LOG.info(
        "source=%s candidates=%d bootstrap=%s notified=%d",
        source.id,
        len(candidates),
        bootstrap,
        notified,
    )
    return processed, notified


def run_once(settings: Settings) -> int:
    store, poller, _, pipeline = build_runtime(settings)
    sources = load_sources(settings.sources_file)
    errors = 0
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(sources)))) as executor:
        futures = {
            executor.submit(poll_source, source, poller, pipeline, datetime.now(timezone.utc)): source
            for source in sources
        }
        for future in as_completed(futures):
            source = futures[future]
            try:
                future.result()
            except Exception:
                errors += 1
                LOG.exception("falló la fuente %s", source.id)
    if not settings.dry_run:
        try:
            synced = SupabaseMirror(
                settings.supabase_url,
                settings.supabase_service_key,
                settings.request_timeout_seconds,
            ).sync(store)
            LOG.info("supabase_synced=%d", synced)
        except Exception:
            LOG.exception("falló la sincronización con Supabase; quedó en la cola local")
    return 1 if errors == len(sources) and sources else 0


def dashboard(settings: Settings) -> int:
    store, _, notifier, _ = build_runtime(settings)
    now_local = datetime.now(timezone.utc).astimezone(settings.timezone)
    output = settings.data_dir / "charts" / f"dashboard-{now_local:%Y%m%d-%H%M}.png"
    generated = generate_dashboard(store, output, now_local)
    if generated:
        notifier.send_photo(generated, f"Resumen nacional de combustibles — {now_local:%d/%m/%Y %H:%M}")
    market = generate_openbb_market_chart(settings.data_dir / "charts" / f"openbb-market-{now_local:%Y%m%d-%H%M}.png")
    if market:
        notifier.send_photo(market, f"OpenBB: Brent, WTI y Stoch RSI — {now_local:%d/%m/%Y %H:%M}")
    if generated is None and market is None:
        LOG.warning("no hubo datos suficientes para crear gráficos")
        return 2
    store.set_meta("last_dashboard_at", datetime.now(timezone.utc).isoformat())
    return 0


def health(settings: Settings) -> int:
    store, _, _, _ = build_runtime(settings)
    with store.connect() as db:
        sources = db.execute(
            "SELECT source_id, checked_at, error_count, last_error FROM source_state ORDER BY source_id"
        ).fetchall()
        items = db.execute("SELECT COUNT(*) AS total FROM items").fetchone()["total"]
        prices = db.execute("SELECT COUNT(*) AS total FROM fuel_prices").fetchone()["total"]
    print(f"items={items} prices={prices} sources={len(sources)} dry_run={settings.dry_run}")
    for row in sources:
        print(
            f"{row['source_id']}: checked={row['checked_at']} errors={row['error_count']} "
            f"last_error={row['last_error'] or '-'}"
        )
    return 0 if all(row["error_count"] < 3 for row in sources) else 1


def run_forever(settings: Settings) -> int:
    store, poller, notifier, pipeline = build_runtime(settings)
    sources = load_sources(settings.sources_file)
    if not sources:
        raise RuntimeError("no hay fuentes activas")

    stop_event = threading.Event()

    def request_stop(*_):
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    def source_worker(source) -> None:
        while not stop_event.is_set():
            started = time.monotonic()
            try:
                poll_source(source, poller, pipeline, datetime.now(timezone.utc))
            except Exception:
                LOG.exception("falló la fuente %s; las demás continúan", source.id)
            state = store.source_state(source.id)
            failures = int(state.get("error_count") or 0)
            base_interval = source.interval_for(datetime.now(timezone.utc).astimezone(settings.timezone))
            interval = min(900, base_interval * (2 ** min(failures, 4)))
            remaining = max(1.0, interval - (time.monotonic() - started))
            stop_event.wait(remaining)

    workers = [
        threading.Thread(target=source_worker, args=(source,), name=f"source-{source.id}", daemon=True)
        for source in sources
    ]
    for worker in workers:
        worker.start()

    mirror = SupabaseMirror(
        settings.supabase_url,
        settings.supabase_service_key,
        settings.request_timeout_seconds,
        enabled=not settings.dry_run,
    )
    next_sync = 0.0

    while not stop_event.is_set():
        utc_now = datetime.now(timezone.utc)
        local_now = utc_now.astimezone(settings.timezone)

        last_raw = store.get_meta("last_dashboard_at")
        try:
            last = datetime.fromisoformat(last_raw) if last_raw else None
        except ValueError:
            last = None
        if last is None or utc_now - last.astimezone(timezone.utc) >= timedelta(hours=settings.dashboard_hours):
            output = settings.data_dir / "charts" / f"dashboard-{local_now:%Y%m%d-%H%M}.png"
            try:
                generated = generate_dashboard(store, output, local_now)
                if generated:
                    notifier.send_photo(generated, f"Resumen nacional de combustibles — {local_now:%d/%m/%Y %H:%M}")
                    store.set_meta("last_dashboard_at", utc_now.isoformat())
            except Exception:
                LOG.exception("falló el gráfico programado")
        if mirror.enabled and time.monotonic() >= next_sync:
            try:
                synced = mirror.sync(store)
                if synced:
                    LOG.info("supabase_synced=%d", synced)
            except Exception:
                LOG.exception("falló Supabase; se reintentará sin perder datos")
            next_sync = time.monotonic() + max(60, settings.supabase_sync_minutes * 60)
        stop_event.wait(5)
    for worker in workers:
        worker.join(timeout=5)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor privado de combustibles para Ecuador")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="revisa todas las fuentes una sola vez")
    mode.add_argument("--dashboard", action="store_true", help="genera y envía el gráfico nacional")
    mode.add_argument("--health", action="store_true", help="muestra salud y errores de fuentes")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings.from_env()
    if args.once:
        return run_once(settings)
    if args.dashboard:
        return dashboard(settings)
    if args.health:
        return health(settings)
    return run_forever(settings)


if __name__ == "__main__":
    sys.exit(main())

