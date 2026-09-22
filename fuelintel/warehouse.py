from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def save_market_history(path: Path, raw_series: dict[str, list[tuple[str, float]]]) -> int:
    """Normalize with Polars and persist compact history in DuckDB."""
    import duckdb
    import polars as pl

    records = [
        {"symbol": symbol, "observed_date": day, "close": value}
        for symbol, rows in raw_series.items()
        for day, value in rows
    ]
    if not records:
        return 0
    frame = pl.DataFrame(records).with_columns(
        pl.col("observed_date").str.to_date(),
        pl.col("close").cast(pl.Float64),
    ).unique(subset=["symbol", "observed_date"], keep="last")
    path.parent.mkdir(parents=True, exist_ok=True)
    retrieved_at = datetime.now(timezone.utc).isoformat()
    with duckdb.connect(str(path)) as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS market_observations (
                symbol VARCHAR NOT NULL,
                observed_date DATE NOT NULL,
                close DOUBLE NOT NULL,
                retrieved_at TIMESTAMPTZ NOT NULL,
                source VARCHAR NOT NULL,
                PRIMARY KEY(symbol, observed_date)
            )
            """
        )
        db.register("incoming_market_rows", frame.to_arrow())
        db.execute(
            """INSERT OR REPLACE INTO market_observations
            SELECT symbol, observed_date, close, ?::TIMESTAMPTZ, 'Yahoo Finance direct'
            FROM incoming_market_rows""",
            [retrieved_at],
        )
    return frame.height
