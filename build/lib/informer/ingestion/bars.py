"""Bar ingestion pipeline and upsert routines.

This module defines functions to fetch OHLCV bar data from a data
provider and persist it into the database.  Persistence is performed
via idempotent upserts so that rerunning ingestion over the same
time range will update existing rows rather than inserting duplicates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List, Optional

from sqlalchemy import Table, Column, MetaData, Text, Float, BigInteger, TIMESTAMP
from sqlalchemy import create_engine, insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

from ..providers.models import Bar

# Define a SQLAlchemy Core table for the bars table.  This mirrors the
# schema defined in the Alembic migration but omits the inserted_at
# column because the database will assign it automatically on insert.
metadata = MetaData()
bars_table = Table(
    "bars",
    metadata,
    Column("symbol", Text, nullable=False),
    Column("timeframe", Text, nullable=False),
    Column("ts", TIMESTAMP(timezone=True), nullable=False),
    Column("open", Float, nullable=False),
    Column("high", Float, nullable=False),
    Column("low", Float, nullable=False),
    Column("close", Float, nullable=False),
    Column("volume", BigInteger, nullable=False),
    Column("vwap", Float, nullable=True),
    Column("source", Text, nullable=False),
)


@dataclass
class IngestStats:
    """Simple data class capturing statistics of an ingestion run."""

    timeframe: str
    symbol_count: int
    bars_fetched: int
    bars_upserted: int
    start: datetime
    end: datetime


def upsert_bars(engine: Engine, bars: List[Bar], chunk_size: int = 2000) -> int:
    """Insert or update bar records into the database.

    This function performs idempotent upserts into the ``bars`` table.  If a
    row with the same ``(symbol, timeframe, ts)`` already exists, the
    ``open``, ``high``, ``low``, ``close``, ``volume``, ``vwap`` and
    ``source`` fields are updated.  The ``inserted_at`` column is left
    untouched on update.

    Args:
        engine: A SQLAlchemy engine connected to the target database.
        bars: A list of :class:`~informer.providers.models.Bar` objects to
            insert or update.
        chunk_size: The maximum number of rows to include in each bulk
            upsert.  Large upserts are chunked to avoid exceeding
            statement size limits.

    Returns:
        The total number of rows affected across all chunks.  Note that
        rowcount semantics are driver dependent and may not be exact.
    """
    if not bars:
        return 0
    # Convert pydantic models to dicts suitable for SQL insertion
    rows: List[dict] = []
    for bar in bars:
        # Only include fields that exist in the table definition
        row = {
            "symbol": bar.symbol,
            "timeframe": bar.timeframe,
            "ts": bar.ts,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
            "vwap": bar.vwap,
            "source": bar.source,
        }
        rows.append(row)
    total_rowcount = 0
    # Use a connection to execute chunks within a single transaction for
    # efficiency.  The caller is responsible for managing the engine's
    # transactional context; we use engine.begin() to ensure commit/rollback.
    with engine.begin() as conn:
        for i in range(0, len(rows), chunk_size):
            chunk = rows[i : i + chunk_size]
            stmt = pg_insert(bars_table).values(chunk)
            # Prepare the update assignments for conflict resolution
            update_dict = {
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "vwap": stmt.excluded.vwap,
                "source": stmt.excluded.source,
            }
            stmt = stmt.on_conflict_do_update(
                index_elements=["symbol", "timeframe", "ts"], set_=update_dict
            )
            result = conn.execute(stmt)
            try:
                total_rowcount += result.rowcount or 0
            except Exception:
                # Some drivers (psycopg3) may not support rowcount on upserts.
                pass
    return total_rowcount


def ingest_timeframe(
    provider,  # type: ignore[type-arg]
    engine: Engine,
    symbols: Iterable[str],
    timeframe: str,
    start: datetime,
    end: datetime,
) -> IngestStats:
    """Fetch bars for a timeframe and upsert them.

    This helper function orchestrates fetching historical bars via the
    provided data provider, performing an idempotent upsert into the
    database, and returning simple statistics about the operation.

    Args:
        provider: An instance of :class:`~informer.providers.base.DataProvider`
            (or compatible) that provides ``get_historical_bars``.
        engine: A SQLAlchemy engine connected to the target database.
        symbols: Iterable of symbol strings to ingest.
        timeframe: The canonical timeframe to ingest (e.g., ``15m``).
        start: Start datetime (inclusive) in UTC.
        end: End datetime (exclusive) in UTC.

    Returns:
        An :class:`IngestStats` summarizing the ingestion.
    """
    symbols_list = list(symbols)
    bars: List[Bar] = provider.get_historical_bars(
        symbols=symbols_list,
        timeframe=timeframe,
        start=start,
        end=end,
    )
    upserted = upsert_bars(engine, bars)
    stats = IngestStats(
        timeframe=timeframe,
        symbol_count=len(symbols_list),
        bars_fetched=len(bars),
        bars_upserted=upserted,
        start=start,
        end=end,
    )
    return stats