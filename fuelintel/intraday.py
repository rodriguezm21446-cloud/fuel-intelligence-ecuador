from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import os
from pathlib import Path
from statistics import median

from .chart_renderer import render_chart
from .charts import own_data_label
from .market_intelligence import ECUADOR_TZ, MATTE, SYMBOLS
from .text import trim_x


@dataclass(frozen=True, slots=True)
class IntradaySignal:
    series: str
    kind: str
    observed_at: datetime
    price: float
    change_pct: float
    robust_score: float
    sample_size: int
    explanation: str

    @property
    def key(self) -> str:
        return f"{self.series}:{self.kind}"


def _robust_z(sample: list[float], value: float) -> float:
    if len(sample) < 20:
        return 0.0
    center = median(sample)
    mad = median(abs(item - center) for item in sample)
    if mad <= 1e-12:
        return 0.0
    return 0.67448975 * (value - center) / mad


def _valid_price(name: str, value: float) -> bool:
    low, high = ((20.0, 250.0) if name in {"WTI", "Brent"} else (0.5, 10.0))
    return math.isfinite(value) and low <= value <= high


def fetch_yahoo_intraday(
    *, period: str = "1d", interval: str = "1m", timeout: int = 15,
) -> dict[str, list[tuple[datetime, float, float | None]]]:
    """Fetch minute bars from Yahoo through yfinance.

    Yahoo may delay or revise quotes. Timestamps are preserved and downstream
    code refuses stale observations instead of relabelling them as real time.
    """
    import pandas as pd
    import yfinance as yf

    symbol_to_name = {symbols[0]: name for name, symbols in SYMBOLS.items()}
    cache_dir = Path(os.getenv("FUELINTEL_DATA_DIR", "data")) / "runtime" / "yfinance-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    yf.set_tz_cache_location(str(cache_dir))
    frame = yf.download(
        list(symbol_to_name), period=period, interval=interval, group_by="ticker",
        auto_adjust=False, prepost=False, threads=True, progress=False,
        timeout=timeout, repair=False,
    )
    output: dict[str, list[tuple[datetime, float, float | None]]] = {}
    if frame is None or frame.empty:
        return output
    for symbol, name in symbol_to_name.items():
        try:
            if isinstance(frame.columns, pd.MultiIndex):
                if symbol in frame.columns.get_level_values(0):
                    subset = frame[symbol]
                else:
                    subset = frame.xs(symbol, axis=1, level=1)
            else:
                if len(symbol_to_name) != 1:
                    continue
                subset = frame
            closes = subset["Close"]
            volumes = subset["Volume"] if "Volume" in subset else None
        except (KeyError, TypeError, ValueError):
            continue
        rows: list[tuple[datetime, float, float | None]] = []
        for stamp, raw_close in closes.dropna().tail(360).items():
            value = float(raw_close)
            if not _valid_price(name, value):
                continue
            moment = stamp.to_pydatetime() if hasattr(stamp, "to_pydatetime") else stamp
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            moment = moment.astimezone(timezone.utc)
            raw_volume = None if volumes is None else volumes.get(stamp)
            volume = None if raw_volume is None or not math.isfinite(float(raw_volume)) else float(raw_volume)
            rows.append((moment, value, volume))
        if len(rows) >= 30:
            output[name] = rows
    return output


def detect_intraday_signals(
    series: dict[str, list[tuple[datetime, float, float | None]]],
    *, now: datetime | None = None,
) -> list[IntradaySignal]:
    """Detect jumps and volatility regimes; never infer their cause."""
    now = now or datetime.now(timezone.utc)
    signals: list[IntradaySignal] = []
    for name, rows in series.items():
        rows = sorted(rows, key=lambda row: row[0])
        if len(rows) < 35 or now - rows[-1][0] > timedelta(minutes=15):
            continue
        prices = [row[1] for row in rows]
        returns = [math.log(current / previous) for previous, current in zip(prices, prices[1:]) if previous > 0]
        if len(returns) < 30:
            continue

        latest_pct = (math.exp(returns[-1]) - 1) * 100
        latest_score = _robust_z(returns[-121:-1], returns[-1])
        if abs(latest_pct) >= 0.35 and abs(latest_score) >= 5:
            direction = "subida" if latest_pct > 0 else "bajada"
            signals.append(IntradaySignal(
                name, "salto_1m", rows[-1][0], prices[-1], latest_pct, latest_score,
                min(120, len(returns) - 1),
                f"{name} registró una {direction} inusual de {latest_pct:+.2f}% en un minuto.",
            ))

        if len(prices) >= 26:
            move_5m = (prices[-1] / prices[-6] - 1) * 100
            historical_5m = [
                math.log(prices[index] / prices[index - 5])
                for index in range(5, len(prices) - 1)
                if prices[index - 5] > 0
            ]
            move_5m_log = math.log(prices[-1] / prices[-6])
            move_score = _robust_z(historical_5m[-120:], move_5m_log)
            if abs(move_5m) >= 0.80 and abs(move_score) >= 4:
                direction = "subió" if move_5m > 0 else "bajó"
                signals.append(IntradaySignal(
                    name, "movimiento_5m", rows[-1][0], prices[-1], move_5m, move_score,
                    min(120, len(historical_5m)),
                    f"{name} {direction} {abs(move_5m):.2f}% en cinco minutos, fuera de su patrón intradía reciente.",
                ))

        if len(returns) >= 50:
            recent = returns[-10:]
            baseline = returns[-130:-10] or returns[:-10]
            recent_rms = math.sqrt(sum(value * value for value in recent) / len(recent))
            baseline_rms = math.sqrt(sum(value * value for value in baseline) / len(baseline))
            ratio = recent_rms / baseline_rms if baseline_rms > 1e-9 else 0.0
            if ratio >= 3 and recent_rms * 100 >= 0.20:
                signals.append(IntradaySignal(
                    name, "regimen_volatil", rows[-1][0], prices[-1], recent_rms * 100,
                    ratio, len(baseline),
                    f"La variación intradía de {name} es {ratio:.1f} veces su nivel reciente.",
                ))

    # Keep one strongest alert per series/kind and avoid a Telegram burst.
    unique: dict[str, IntradaySignal] = {}
    for signal in signals:
        previous = unique.get(signal.key)
        if previous is None or abs(signal.robust_score) > abs(previous.robust_score):
            unique[signal.key] = signal
    return sorted(unique.values(), key=lambda item: abs(item.robust_score), reverse=True)[:3]


class IntradayAlertGate:
    """Require repeated detection and enforce a durable cooldown."""

    def __init__(self, confirmations: int = 2, cooldown_minutes: int = 30):
        self.confirmations = max(2, confirmations)
        self.cooldown = timedelta(minutes=max(5, cooldown_minutes))
        self._pending: dict[str, tuple[datetime, int]] = {}

    def accept(self, signal: IntradaySignal, store, now: datetime) -> bool:
        previous = self._pending.get(signal.key)
        count = previous[1] + 1 if previous and previous[0] == signal.observed_at else 1
        self._pending[signal.key] = (signal.observed_at, count)
        if count < self.confirmations:
            return False
        raw_last = store.get_meta(f"intraday_alert_{signal.key}")
        try:
            last = datetime.fromisoformat(raw_last) if raw_last else None
        except ValueError:
            last = None
        if last and now - last.astimezone(timezone.utc) < self.cooldown:
            return False
        store.set_meta(f"intraday_alert_{signal.key}", now.isoformat())
        return True


def build_intraday_chart_payload(
    series: dict[str, list[tuple[datetime, float, float | None]]], signal: IntradaySignal,
    verified_at: datetime,
) -> dict:
    rows = series[signal.series][-120:]
    unit = "USD/barril" if signal.series in {"WTI", "Brent"} else "USD/galón"
    decimals = 2 if signal.series in {"WTI", "Brent"} else 3
    labels = [moment.astimezone(ECUADOR_TZ).strftime("%H:%M") for moment, _, _ in rows]
    return {
        "title": f"Movimiento intradía de {signal.series}",
        "subtitle": f"Intervalo de un minuto · {unit} · hora de Ecuador",
        "labels": labels,
        "datasets": [{
            "label": signal.series,
            "color": MATTE[signal.series],
            "values": [price for _, price, _ in rows],
            "legendValue": f"${rows[-1][1]:.{decimals}f}",
            "pending": False,
        }],
        "annotations": [{"index": len(labels) - 1, "text": signal.kind.replace("_", " ")}],
        "references": [],
        "chartKind": "line",
        "showEndpointLabels": False,
        "insight": signal.explanation,
        "source": (
            f"Datos: Yahoo Finance vía yfinance · Última observación: "
            f"{rows[-1][0].astimezone(ECUADOR_TZ):%d/%m/%Y %H:%M:%S} · "
            f"Verificado: {verified_at.astimezone(ECUADOR_TZ):%d/%m/%Y %H:%M:%S} · "
            "cotización intradía; puede ser diferida o revisada"
        ),
        "credit": own_data_label(),
        "displayDecimals": decimals,
        "valuePrefix": "$",
        "valueSuffix": "",
    }


def build_intraday_messages(signal: IntradaySignal, news: list[dict], verified_at: datetime) -> tuple[str, str]:
    unit = "/barril" if signal.series in {"WTI", "Brent"} else "/gal"
    decimals = 2 if signal.series in {"WTI", "Brent"} else 3
    squawk = trim_x(
        f"Alerta de mercado | {signal.series} ${signal.price:.{decimals}f}{unit}: "
        f"{signal.change_pct:+.2f}% ({'1 min' if signal.kind == 'salto_1m' else 'ventana intradía'}). "
        "Movimiento estadísticamente inusual. Fuente: Yahoo Finance; dato puede ser diferido.",
        300,
    )
    lines = [
        "ANÁLISIS DE LA ALERTA",
        signal.explanation,
        f"Muestra de comparación: {signal.sample_size} observaciones.",
        f"Puntuación robusta: {signal.robust_score:+.2f}.",
        f"Observado: {signal.observed_at.astimezone(ECUADOR_TZ):%d/%m/%Y %H:%M:%S} · hora de Ecuador (UTC−5).",
        f"Verificado: {verified_at.astimezone(ECUADOR_TZ):%d/%m/%Y %H:%M:%S}.",
    ]
    if news:
        lines.extend(["Noticias recientes revisadas:"])
        for story in news[:2]:
            lines.append(f"{story['source_name']}: {story['title']}")
            lines.append(story["canonical_url"])
        lines.append("La coincidencia temporal no demuestra que esas noticias causaron el movimiento.")
    else:
        lines.append("No se encontró una noticia reciente aprobada que explique el movimiento.")
    lines.append("Esto activa revisión; no es una predicción ni un cambio confirmado para Ecuador.")
    return squawk, "\n".join(lines)


class IntradayMonitor:
    def __init__(self, store, notifier, data_dir: Path, *, cooldown_minutes: int = 30):
        self.store = store
        self.notifier = notifier
        self.data_dir = data_dir
        self.gate = IntradayAlertGate(cooldown_minutes=cooldown_minutes)

    def scan(self, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        series = fetch_yahoo_intraday()
        for name, rows in series.items():
            moment, price, volume = rows[-1]
            self.store.save_market_tick(name, SYMBOLS[name][0], moment, price, volume, now)
        emitted = 0
        for signal in detect_intraday_signals(series, now=now):
            if not self.gate.accept(signal, self.store, now):
                continue
            news = self.store.recent_notified_market_news(now=now, hours=6, limit=2)
            payload = build_intraday_chart_payload(series, signal, now)
            output = self.data_dir / "charts" / "intraday" / f"{signal.series.lower()}-{now:%Y%m%d-%H%M%S}.png"
            try:
                chart = render_chart(payload, output)
                self.notifier.send_photo(
                    chart,
                    f"Alerta intradía de {signal.series} · {now.astimezone(ECUADOR_TZ):%d/%m/%Y %H:%M} · hora de Ecuador",
                )
            except Exception as error:
                self.store.record_chart_error("WF_06_CHART_GENERATOR", f"intradía: {type(error).__name__}: {error}")
            for message in build_intraday_messages(signal, news, now):
                self.notifier.send_text(message)
            emitted += 1
        return emitted
