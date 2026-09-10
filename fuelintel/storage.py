from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator
from decimal import Decimal

from .models import Candidate, Decision, FuelPrice
from .text import canonical_url, fold, title_similarity


SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash TEXT NOT NULL UNIQUE,
    canonical_url TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_trust INTEGER NOT NULL,
    title TEXT NOT NULL,
    normalized_title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    published_at TEXT,
    detected_at TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence INTEGER NOT NULL,
    reasons_json TEXT NOT NULL,
    relevance_score INTEGER NOT NULL,
    notified_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_detected ON items(detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_items_url ON items(canonical_url);

CREATE TABLE IF NOT EXISTS fuel_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product TEXT NOT NULL,
    price TEXT NOT NULL,
    currency TEXT NOT NULL,
    unit TEXT NOT NULL,
    effective_from TEXT,
    effective_to TEXT,
    source_url TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    verified_at TEXT,
    price_type TEXT NOT NULL DEFAULT 'UNKNOWN',
    source_name TEXT NOT NULL DEFAULT '',
    decree_resolution TEXT NOT NULL DEFAULT '',
    source_conflict INTEGER NOT NULL DEFAULT 0,
    verification_status TEXT NOT NULL DEFAULT 'CONFIRMED',
    UNIQUE(product, price, effective_from, effective_to, source_url)
);
CREATE INDEX IF NOT EXISTS idx_prices_product_time ON fuel_prices(product, observed_at DESC);

CREATE TABLE IF NOT EXISTS fuel_price_metrics (
    product TEXT NOT NULL,
    price_type TEXT NOT NULL,
    historical_max TEXT NOT NULL,
    historical_max_date TEXT NOT NULL,
    historical_min TEXT NOT NULL,
    historical_min_date TEXT NOT NULL,
    latest_price TEXT NOT NULL,
    latest_effective_from TEXT,
    mom_absolute TEXT,
    mom_percent TEXT,
    yoy_absolute TEXT,
    yoy_percent TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(product, price_type)
);

CREATE TABLE IF NOT EXISTS source_state (
    source_id TEXT PRIMARY KEY,
    etag TEXT,
    last_modified TEXT,
    content_hash TEXT,
    checked_at TEXT,
    error_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    synced_at TEXT,
    UNIQUE(entity_type, dedupe_key)
);
CREATE INDEX IF NOT EXISTS idx_outbox_pending ON sync_outbox(synced_at, id);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            self._migrate_price_columns(db)

    @staticmethod
    def _migrate_price_columns(db: sqlite3.Connection) -> None:
        columns = {row["name"] for row in db.execute("PRAGMA table_info(fuel_prices)")}
        additions = {
            "verified_at": "TEXT",
            "price_type": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
            "source_name": "TEXT NOT NULL DEFAULT ''",
            "decree_resolution": "TEXT NOT NULL DEFAULT ''",
            "source_conflict": "INTEGER NOT NULL DEFAULT 0",
            "verification_status": "TEXT NOT NULL DEFAULT 'CONFIRMED'",
        }
        for name, definition in additions.items():
            if name not in columns:
                db.execute(f"ALTER TABLE fuel_prices ADD COLUMN {name} {definition}")
        db.execute("UPDATE fuel_prices SET verified_at = observed_at WHERE verified_at IS NULL")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def is_duplicate(self, candidate: Candidate, similarity_threshold: float = 0.78) -> tuple[bool, str]:
        url = canonical_url(candidate.url)
        cutoff = (candidate.detected_at - timedelta(days=7)).isoformat()
        with self.connect() as db:
            exact = db.execute(
                "SELECT title FROM items WHERE content_hash = ? LIMIT 1",
                (candidate.content_hash,),
            ).fetchone()
            if exact:
                return True, "huella exacta ya procesada"
            rows = db.execute(
                "SELECT title FROM items WHERE detected_at >= ? AND canonical_url != ? ORDER BY detected_at DESC LIMIT 300",
                (cutoff, url),
            ).fetchall()
        for row in rows:
            if title_similarity(candidate.title, row["title"]) >= similarity_threshold:
                return True, "titular equivalente ya procesado"
        return False, "contenido nuevo"

    def save_item(self, candidate: Candidate, decision: Decision) -> int:
        with self.connect() as db:
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO items (
                    content_hash, canonical_url, source_id, source_name, source_trust,
                    title, normalized_title, summary, published_at, detected_at,
                    status, confidence, reasons_json, relevance_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.content_hash,
                    canonical_url(candidate.url),
                    candidate.source.id,
                    candidate.source.name,
                    candidate.source.trust,
                    candidate.title,
                    fold(candidate.title),
                    candidate.summary,
                    candidate.published_at.isoformat() if candidate.published_at else None,
                    candidate.detected_at.isoformat(),
                    decision.status.value,
                    decision.confidence,
                    json.dumps(decision.reasons, ensure_ascii=False),
                    decision.relevance_score,
                ),
            )
            item_id = int(cursor.lastrowid or 0)
            if cursor.rowcount > 0:
                payload = {
                    "content_hash": candidate.content_hash,
                    "canonical_url": canonical_url(candidate.url),
                    "source_id": candidate.source.id,
                    "source_name": candidate.source.name,
                    "title": candidate.title,
                    "published_at": candidate.published_at.isoformat() if candidate.published_at else None,
                    "detected_at": candidate.detected_at.isoformat(),
                    "status": decision.status.value,
                    "confidence": decision.confidence,
                    "reasons": list(decision.reasons),
                    "notified_at": None,
                }
                self._enqueue(db, "items", candidate.content_hash, payload)
            return item_id

    def has_corroboration(self, candidate: Candidate, hours: int = 12) -> bool:
        cutoff = (candidate.detected_at - timedelta(hours=hours)).isoformat()
        with self.connect() as db:
            rows = db.execute(
                "SELECT source_id, title FROM items WHERE detected_at >= ? AND source_id != ?",
                (cutoff, candidate.source.id),
            ).fetchall()
        return any(title_similarity(candidate.title, row["title"]) >= 0.52 for row in rows)

    def mark_notified(self, item_id: int, when: datetime | None = None) -> None:
        if not item_id:
            return
        when = when or datetime.now(timezone.utc)
        with self.connect() as db:
            db.execute("UPDATE items SET notified_at = ? WHERE id = ?", (when.isoformat(), item_id))

    def save_price(self, price: FuelPrice) -> bool:
        with self.connect() as db:
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO fuel_prices (
                    product, price, currency, unit, effective_from, effective_to,
                    source_url, observed_at, verified_at, price_type, source_name,
                    decree_resolution, source_conflict, verification_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    price.product,
                    str(price.price),
                    price.currency,
                    price.unit,
                    price.period.start.isoformat() if price.period else None,
                    price.period.end.isoformat() if price.period else None,
                    canonical_url(price.source_url),
                    price.verified_at.isoformat(),
                    price.observed_at.isoformat(),
                    price.price_type.value,
                    price.source_name,
                    price.decree_resolution,
                    int(price.source_conflict),
                    price.verification_status,
                ),
            )
            inserted = cursor.rowcount > 0
            if inserted:
                self._refresh_price_metrics(db, price.product, price.price_type.value)
                effective_from = price.period.start.isoformat() if price.period else None
                effective_to = price.period.end.isoformat() if price.period else None
                payload = {
                    "product": price.product,
                    "price": str(price.price),
                    "currency": price.currency,
                    "unit": price.unit,
                    "effective_from": effective_from,
                    "effective_to": effective_to,
                    "source_url": canonical_url(price.source_url),
                    "observed_at": price.observed_at.isoformat(),
                    "verified_at": price.verified_at.isoformat(),
                    "price_type": price.price_type.value,
                    "source_name": price.source_name,
                    "decree_resolution": price.decree_resolution or None,
                    "source_conflict": price.source_conflict,
                    "verification_status": price.verification_status,
                }
                key = "|".join([price.product, str(price.price), effective_from or "", effective_to or "", canonical_url(price.source_url)])
                self._enqueue(db, "prices", key, payload)
            return inserted

    @staticmethod
    def _change(current: Decimal, previous: Decimal | None) -> tuple[str | None, str | None]:
        if previous is None:
            return None, None
        absolute = current - previous
        percent = None if previous == 0 else absolute / previous * Decimal("100")
        return str(absolute.quantize(Decimal("0.001"))), str(percent.quantize(Decimal("0.01"))) if percent is not None else None

    def _refresh_price_metrics(self, db: sqlite3.Connection, product: str, price_type: str) -> None:
        rows = db.execute(
            """
            SELECT price, effective_from, observed_at FROM fuel_prices
            WHERE product = ? AND price_type = ? AND source_conflict = 0
              AND verification_status = 'CONFIRMED'
            ORDER BY COALESCE(effective_from, observed_at), verified_at
            """,
            (product, price_type),
        ).fetchall()
        if not rows:
            db.execute("DELETE FROM fuel_price_metrics WHERE product = ? AND price_type = ?", (product, price_type))
            self._enqueue(db, "metrics_delete", f"{product}|{price_type}", {"product": product, "price_type": price_type})
            return
        latest = rows[-1]
        current = Decimal(latest["price"])
        previous = Decimal(rows[-2]["price"]) if len(rows) > 1 else None
        mom_absolute, mom_percent = self._change(current, previous)
        latest_date = latest["effective_from"] or latest["observed_at"][:10]
        latest_period = date.fromisoformat(latest_date)
        target_year = latest_period.year - 1
        yoy_row = next(
            (
                row
                for row in reversed(rows[:-1])
                if (comparison_date := date.fromisoformat(row["effective_from"] or row["observed_at"][:10])).year
                == target_year
                and comparison_date.month == latest_period.month
            ),
            None,
        )
        yoy_absolute, yoy_percent = self._change(current, Decimal(yoy_row["price"]) if yoy_row else None)
        maximum = max(rows, key=lambda row: Decimal(row["price"]))
        minimum = min(rows, key=lambda row: Decimal(row["price"]))
        db.execute(
            """
            INSERT INTO fuel_price_metrics(
                product, price_type, historical_max, historical_max_date,
                historical_min, historical_min_date, latest_price, latest_effective_from,
                mom_absolute, mom_percent, yoy_absolute, yoy_percent, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(product, price_type) DO UPDATE SET
                historical_max=excluded.historical_max,
                historical_max_date=excluded.historical_max_date,
                historical_min=excluded.historical_min,
                historical_min_date=excluded.historical_min_date,
                latest_price=excluded.latest_price,
                latest_effective_from=excluded.latest_effective_from,
                mom_absolute=excluded.mom_absolute,
                mom_percent=excluded.mom_percent,
                yoy_absolute=excluded.yoy_absolute,
                yoy_percent=excluded.yoy_percent,
                updated_at=excluded.updated_at
            """,
            (
                product,
                price_type,
                maximum["price"],
                maximum["effective_from"] or maximum["observed_at"][:10],
                minimum["price"],
                minimum["effective_from"] or minimum["observed_at"][:10],
                latest["price"],
                latest["effective_from"],
                mom_absolute,
                mom_percent,
                yoy_absolute,
                yoy_percent,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        metric_payload = {
            "product": product,
            "price_type": price_type,
            "historical_max": maximum["price"],
            "historical_max_date": maximum["effective_from"] or maximum["observed_at"][:10],
            "historical_min": minimum["price"],
            "historical_min_date": minimum["effective_from"] or minimum["observed_at"][:10],
            "latest_price": latest["price"],
            "latest_effective_from": latest["effective_from"],
            "mom_absolute": mom_absolute,
            "mom_percent": mom_percent,
            "yoy_absolute": yoy_absolute,
            "yoy_percent": yoy_percent,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._enqueue(db, "metrics", f"{product}|{price_type}", metric_payload)

    @staticmethod
    def _enqueue(db: sqlite3.Connection, entity_type: str, dedupe_key: str, payload: dict) -> None:
        opposite = {"metrics": "metrics_delete", "metrics_delete": "metrics"}.get(entity_type)
        if opposite:
            db.execute(
                "DELETE FROM sync_outbox WHERE entity_type = ? AND dedupe_key = ?",
                (opposite, dedupe_key),
            )
        db.execute(
            """
            INSERT INTO sync_outbox(entity_type, dedupe_key, payload_json, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(entity_type, dedupe_key) DO UPDATE SET
                payload_json=excluded.payload_json,
                created_at=excluded.created_at,
                attempts=0,
                last_error=NULL,
                synced_at=NULL
            """,
            (entity_type, dedupe_key, json.dumps(payload, ensure_ascii=False), datetime.now(timezone.utc).isoformat()),
        )

    def pending_sync(self, limit: int = 300) -> list[sqlite3.Row]:
        with self.connect() as db:
            return db.execute(
                "SELECT id, entity_type, payload_json FROM sync_outbox WHERE synced_at IS NULL ORDER BY id LIMIT ?",
                (limit,),
            ).fetchall()

    def mark_synced(self, ids: list[int]) -> None:
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as db:
            db.execute(
                f"UPDATE sync_outbox SET synced_at = ?, last_error = NULL WHERE id IN ({placeholders})",
                [datetime.now(timezone.utc).isoformat(), *ids],
            )

    def mark_sync_error(self, ids: list[int], error: str) -> None:
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as db:
            db.execute(
                f"UPDATE sync_outbox SET attempts = attempts + 1, last_error = ? WHERE id IN ({placeholders})",
                [error[:500], *ids],
            )

    def previous_price(self, product: str, exclude_period_start: str | None = None) -> str | None:
        query = "SELECT price FROM fuel_prices WHERE product = ? AND source_conflict = 0 AND verification_status = 'CONFIRMED'"
        values: list[str] = [product]
        if exclude_period_start:
            query += " AND (effective_from IS NULL OR effective_from != ?)"
            values.append(exclude_period_start)
        query += " ORDER BY COALESCE(effective_from, observed_at) DESC, observed_at DESC LIMIT 1"
        with self.connect() as db:
            row = db.execute(query, values).fetchone()
        return row["price"] if row else None

    def recent_prices(self, limit_per_product: int = 24) -> list[sqlite3.Row]:
        with self.connect() as db:
            return db.execute(
                """
                SELECT * FROM (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY product ORDER BY COALESCE(effective_from, observed_at) DESC) AS rn
                    FROM fuel_prices
                    WHERE price_type != 'UNKNOWN' AND source_conflict = 0
                ) WHERE rn <= ? ORDER BY product, COALESCE(effective_from, observed_at)
                """,
                (limit_per_product,),
            ).fetchall()

    def price_conflict(self, price: FuelPrice) -> bool:
        if not price.period or not price.source_name:
            return False
        with self.connect() as db:
            row = db.execute(
                """
                SELECT 1 FROM fuel_prices
                WHERE product = ? AND effective_from = ? AND price_type = ?
                  AND source_name != '' AND source_name != ?
                  AND ABS(CAST(price AS REAL) - CAST(? AS REAL)) > 0.0005
                LIMIT 1
                """,
                (
                    price.product,
                    price.period.start.isoformat(),
                    price.price_type.value,
                    price.source_name,
                    str(price.price),
                ),
            ).fetchone()
        return row is not None

    def mark_source_conflict(self, price: FuelPrice) -> None:
        if not price.period:
            return
        with self.connect() as db:
            db.execute(
                """
                UPDATE fuel_prices SET source_conflict = 1
                WHERE product = ? AND effective_from = ? AND price_type = ?
                """,
                (price.product, price.period.start.isoformat(), price.price_type.value),
            )
            self._refresh_price_metrics(db, price.product, price.price_type.value)

    def price_history(self, product: str, price_type: str) -> list[sqlite3.Row]:
        with self.connect() as db:
            return db.execute(
                """
                SELECT * FROM fuel_prices
                WHERE product = ? AND price_type = ? AND source_conflict = 0
                  AND verification_status = 'CONFIRMED'
                ORDER BY COALESCE(effective_from, observed_at), verified_at
                """,
                (product, price_type),
            ).fetchall()

    def price_metrics(self) -> list[sqlite3.Row]:
        with self.connect() as db:
            return db.execute("SELECT * FROM fuel_price_metrics ORDER BY product, price_type").fetchall()

    def record_chart_error(self, stage: str, error: str) -> None:
        payload = json.dumps(
            {"stage": stage, "error": error[:1000], "at": datetime.now(timezone.utc).isoformat()},
            ensure_ascii=False,
        )
        self.set_meta("WF_08_ERROR_MONITOR", payload)

    def record_pipeline_stage(self, stage: str, status: str, detail: str = "") -> None:
        payload = json.dumps(
            {
                "stage": stage,
                "status": status,
                "detail": detail[:500],
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
        )
        self.set_meta(stage, payload)

    def source_state(self, source_id: str) -> dict[str, str | int | None]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM source_state WHERE source_id = ?", (source_id,)).fetchone()
        return dict(row) if row else {}

    def update_source_state(
        self,
        source_id: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        content_hash: str | None = None,
        error: str | None = None,
    ) -> None:
        old = self.source_state(source_id)
        errors = int(old.get("error_count") or 0) + (1 if error else 0)
        if not error:
            errors = 0
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO source_state(source_id, etag, last_modified, content_hash, checked_at, error_count, last_error)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    etag=excluded.etag,
                    last_modified=excluded.last_modified,
                    content_hash=excluded.content_hash,
                    checked_at=excluded.checked_at,
                    error_count=excluded.error_count,
                    last_error=excluded.last_error
                """,
                (
                    source_id,
                    etag or old.get("etag"),
                    last_modified or old.get("last_modified"),
                    content_hash or old.get("content_hash"),
                    datetime.now(timezone.utc).isoformat(),
                    errors,
                    error,
                ),
            )

    def get_meta(self, key: str) -> str | None:
        with self.connect() as db:
            row = db.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO metadata(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

