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
from .charts import generate_dashboard, generate_historical_dashboard, national_data_signature
from .market_intelligence import build_market_payload, generate_event_market_chart, generate_market_bundle, load_market_series
from .intraday import IntradayMonitor
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
    pipeline = Pipeline(
        store,
        poller,
        notifier,
        settings.timezone,
        editor=editor,
        chart_generator=generate_dashboard,
        market_chart_generator=generate_event_market_chart,
    )
    return store, poller, notifier, pipeline


def poll_source(source, poller: SourcePoller, pipeline: Pipeline, now: datetime) -> tuple[int, int]:
    candidates, bootstrap = poller.poll(source, now)
    # Deliver the newest publications first so a fresh item is never delayed
    # behind an older one that changed in the same poll. Items without a
    # verifiable date fall back to the moment they were detected.
    candidates = sorted(
        candidates,
        key=lambda candidate: candidate.published_at or candidate.detected_at,
        reverse=True,
    )
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


def dashboard(settings: Settings, now_local: datetime | None = None) -> int:
    store, _, notifier, _ = build_runtime(settings)
    now_local = now_local or datetime.now(timezone.utc).astimezone(settings.timezone)
    national_signature = national_data_signature(store)
    if national_signature and national_signature != store.get_meta("last_national_chart_signature"):
        output = settings.data_dir / "charts" / f"dashboard-{now_local:%Y%m%d-%H%M}.png"
        try:
            generated = generate_dashboard(store, output, now_local)
            if generated and notifier.send_photo(
                generated, f"Resumen nacional de combustibles — {now_local:%d/%m/%Y %H:%M} · hora de Ecuador (UTC−5)"
            ):
                store.set_meta("last_national_chart_signature", national_signature)
        except Exception as error:
            store.record_chart_error("WF_06_CHART_GENERATOR", f"nacional: {type(error).__name__}: {error}")
            LOG.exception("WF_08_ERROR_MONITOR registró el fallo del gráfico nacional")
    try:
        related_news = store.recent_notified_market_news(now=datetime.now(timezone.utc), hours=36, limit=2)
        try:
            from .bce import fetch_ecuador_crude_exports
            bce_exports = fetch_ecuador_crude_exports()
        except Exception:
            LOG.exception("no se pudieron leer las exportaciones de crudo del BCE")
            bce_exports = []
        try:
            official_prices = {str(row["product"]): float(row["price"]) for row in store.recent_prices()}
        except Exception:
            LOG.exception("no se pudieron leer los precios oficiales para la brecha")
            official_prices = {}
        market_artifacts, market_sections, market_focus, latest_close = generate_market_bundle(
            settings.data_dir / "charts" / "market-close",
            related_news=related_news,
            warehouse_path=settings.data_dir / "market-history.duckdb",
            eia_api_key=settings.eia_api_key,
            selected_only=True,
            variety=True,
            bce_exports=bce_exports,
            official_prices=official_prices,
        )
    except Exception as error:
        store.record_chart_error("WF_06_CHART_GENERATOR", f"mercado: {type(error).__name__}: {error}")
        LOG.exception("WF_08_ERROR_MONITOR registró el fallo del gráfico de mercado")
        market_artifacts = []
        market_sections = None
        market_focus = "products"
        latest_close = ""
    sent_market_chart = False
    chart_revision = str(getattr(settings, "chart_revision", "1"))
    delivery_day = now_local.date().isoformat()
    if market_artifacts:
        captions = {
            "crude": "WTI y Brent", "products": "Gasolina RBOB y diésel ULSD",
            "volatility": "Volatilidad realizada WTI y Brent", "heatmap": "Mapa de calor de combustibles",
            "weekly-moves": "Variación semanal de energía", "us-retail": "Precios minoristas oficiales de EE. UU.",
            "spread": "Diferencial Brent–WTI", "indexed": "Evolución relativa de energía",
            "ecuador-production": "Producción de petróleo crudo de Ecuador",
            "ecuador-exports": "Exportaciones de crudo de Ecuador",
            "crack-spread": "Margen de refino (crack spread)",
            "price-gap": "Brecha de referencia del precio",
        }
        observed_label = f"Último dato observado: {latest_close}" if latest_close else "Sin fecha de observación comprobable"
        for key, market_path, market_signature in market_artifacts:
            # Send at least once per Ecuador calendar day.  Within the same day,
            # repeat only when verified data or the chart template changed.
            delivery_signature = f"{market_signature}:{delivery_day}:r{chart_revision}"
            if store.get_meta(f"last_market_{key}_signature") == delivery_signature:
                LOG.info("gráfico %s ya enviado hoy y sin cambios; se omite", key)
                continue
            try:
                accepted = notifier.send_photo(
                    market_path,
                    f"Mercado: {captions[key]} · {observed_label} · {now_local:%d/%m/%Y %H:%M} hora de Ecuador",
                )
                if accepted:
                    sent_market_chart = True
                    store.set_meta(f"last_market_{key}_signature", delivery_signature)
                    store.set_meta("last_market_chart_error", "")
            except Exception as error:
                store.record_chart_error("WF_06_CHART_GENERATOR", f"envío programado: {type(error).__name__}: {error}")
                LOG.exception("falló el envío programado del gráfico %s", key)
                return 2
        if sent_market_chart and market_sections:
            for index, message in enumerate(market_sections.messages()):
                # Each squawk is an independent message. One failed delivery must
                # never swallow the remaining sections, so every send is isolated.
                try:
                    notifier.send_text(message)
                except Exception as error:
                    store.record_pipeline_stage(
                        "WF_08_ERROR_MONITOR",
                        "market_section_error",
                        f"text-{index}: {type(error).__name__}: {error}",
                    )
                    LOG.exception("falló el envío de una sección del resumen de mercado")
        if latest_close:
            store.set_meta("last_market_observation", latest_close)
        if not sent_market_chart:
            LOG.info("no hubo envío: todos los gráficos ya se entregaron hoy sin cambios")
    elif store.get_meta("last_market_chart_error") != "sin_datos":
        notifier.send_text("ALERTA TÉCNICA: no llegaron datos de mercado suficientes. El gráfico no se publicó y se reintentará en la próxima ejecución programada.")
        store.set_meta("last_market_chart_error", "sin_datos")
    if not market_artifacts:
        LOG.warning("no hubo datos suficientes para crear gráficos")
        return 2
    store.set_meta("last_dashboard_at", datetime.now(timezone.utc).isoformat())
    return 0


def historical_dashboard(settings: Settings) -> int:
    """Genera el histórico completo únicamente cuando el operador lo solicita."""
    store, _, notifier, _ = build_runtime(settings)
    now_local = datetime.now(timezone.utc).astimezone(settings.timezone)
    output = settings.data_dir / "charts" / f"historico-{now_local:%Y%m%d-%H%M}.png"
    try:
        generated = generate_historical_dashboard(store, output)
    except Exception as error:
        store.record_chart_error("WF_06_CHART_GENERATOR", f"histórico: {type(error).__name__}: {error}")
        LOG.exception("WF_08_ERROR_MONITOR registró el fallo del histórico")
        return 2
    if generated is None:
        LOG.warning("no hay datos confirmados para el histórico")
        return 2
    store.record_pipeline_stage("WF_06_CHART_GENERATOR", "completed", str(generated))
    return 0 if notifier.send_photo(generated, f"Histórico de combustibles · {now_local:%d/%m/%Y %H:%M} · hora de Ecuador (UTC−5)") else 1


def interactive_market(settings: Settings) -> int:
    """Exporta un workspace bqplot local; no envía ni publica nada."""
    from .bqplot_charts import export_bqplot_html

    rows = load_market_series()
    payload = build_market_payload({key: value for key, value in rows.items() if key in {"WTI", "Brent"}})
    if payload is None:
        LOG.warning("no hay datos verificables para el workspace interactivo")
        return 2
    now_local = datetime.now(timezone.utc).astimezone(settings.timezone)
    destination = settings.data_dir / "interactive" / f"market-{now_local:%Y%m%d-%H%M}.html"
    print(export_bqplot_html(payload, destination).resolve())
    return 0


def health(settings: Settings) -> int:
    store, _, _, _ = build_runtime(settings)
    latest_ai = store.latest_ai_run()
    with store.connect() as db:
        sources = db.execute(
            "SELECT source_id, checked_at, error_count, last_error FROM source_state ORDER BY source_id"
        ).fetchall()
        items = db.execute("SELECT COUNT(*) AS total FROM items").fetchone()["total"]
        prices = db.execute("SELECT COUNT(*) AS total FROM fuel_prices").fetchone()["total"]
        ticks = db.execute("SELECT COUNT(*) AS total FROM market_ticks").fetchone()["total"]
        latest_tick = db.execute("SELECT MAX(observed_at) AS latest FROM market_ticks").fetchone()["latest"]
    print(
        f"items={items} prices={prices} market_ticks={ticks} latest_market_tick={latest_tick or '-'} "
        f"sources={len(sources)} dry_run={settings.dry_run} intraday_enabled={settings.intraday_enabled} "
        f"intraday_interval_seconds={settings.intraday_interval_seconds}"
    )
    if not settings.gemini_api_key:
        print("gemini=not_configured editorial_fallback=deterministic")
    elif latest_ai is None:
        print(f"gemini=configured model={settings.gemini_model} status=no_runs")
    else:
        error = str(latest_ai.get("error") or "-").replace("\n", " ")[:180]
        print(
            "gemini="
            f"{latest_ai.get('status', 'unknown')} model={latest_ai.get('model', settings.gemini_model)} "
            f"purpose={latest_ai.get('purpose', '-')} at={latest_ai.get('created_at', '-')} error={error}"
        )
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
    if settings.intraday_enabled:
        intraday = IntradayMonitor(
            store,
            notifier,
            settings.data_dir,
            cooldown_minutes=settings.intraday_alert_cooldown_minutes,
        )

        def intraday_worker() -> None:
            failures = 0
            while not stop_event.is_set():
                started = time.monotonic()
                try:
                    emitted = intraday.scan(datetime.now(timezone.utc))
                    failures = 0
                    if emitted:
                        LOG.info("intraday_alerts=%d", emitted)
                except Exception as error:
                    failures += 1
                    store.record_pipeline_stage(
                        "WF_08_ERROR_MONITOR",
                        "intraday_error",
                        f"{type(error).__name__}: {error}",
                    )
                    LOG.exception("falló la vigilancia intradía de Yahoo Finance")
                base = settings.intraday_interval_seconds
                interval = min(900, base * (2 ** min(failures, 4)))
                stop_event.wait(max(1.0, interval - (time.monotonic() - started)))

        workers.append(threading.Thread(target=intraday_worker, name="market-intraday", daemon=True))
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
            try:
                dashboard(settings)
            except Exception:
                LOG.exception("falló el resumen diario programado")
            finally:
                # A failed provider must not start a tight retry/render loop.
                store.set_meta("last_dashboard_at", utc_now.isoformat())
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
    mode.add_argument("--historical-dashboard", action="store_true", help="genera el histórico completo bajo demanda")
    mode.add_argument("--interactive-market", action="store_true", help="exporta un gráfico bqplot interactivo a HTML")
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
    if args.historical_dashboard:
        return historical_dashboard(settings)
    if args.interactive_market:
        return interactive_market(settings)
    if args.health:
        return health(settings)
    return run_forever(settings)


if __name__ == "__main__":
    sys.exit(main())
