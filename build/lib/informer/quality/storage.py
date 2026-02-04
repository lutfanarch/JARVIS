"""Storage utilities for data quality events.

This module defines the SQLAlchemy Core representation of the
``data_quality_events`` table and provides a helper for bulk
insertion of :class:`~informer.quality.checks.DataQualityEvent`
instances.  The table schema mirrors the Alembic migration with the
``inserted_at`` column omitted from insertion to allow the database
default to apply.
"""

from __future__ import annotations

from typing import Iterable, List

from sqlalchemy import Column, MetaData, Table, Text, TIMESTAMP
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from .checks import DataQualityEvent

# Define a dedicated metadata and table for quality events.  This avoids
# interfering with other table definitions and allows tests to create
# the table in isolation.
metadata = MetaData()
data_quality_events_table = Table(
    "data_quality_events",
    metadata,
    Column("run_id", Text, nullable=False),
    Column("symbol", Text, nullable=False),
    Column("timeframe", Text, nullable=False),
    Column("ts", TIMESTAMP(timezone=True), nullable=False),
    Column("severity", Text, nullable=False),
    Column("code", Text, nullable=False),
    Column("message", Text, nullable=False),
    # inserted_at column exists in the database with a default, but we do not
    # include it here so that the default is used automatically.
)


def insert_quality_events(
    engine: Engine, events: Iterable[DataQualityEvent], chunk_size: int = 2000
) -> int:
    """Insert one or more data quality events into the database.

    Args:
        engine: A SQLAlchemy engine bound to the target database.
        events: An iterable of :class:`DataQualityEvent` instances.
        chunk_size: Maximum number of rows per insert statement.

    Returns:
        Approximate number of rows inserted.  Rowcount semantics may vary.
    """
    events_list: List[DataQualityEvent] = list(events)
    if not events_list:
        return 0
    total = 0
    with engine.begin() as conn:
        for i in range(0, len(events_list), chunk_size):
            chunk = events_list[i : i + chunk_size]
            rows = [
                {
                    "run_id": ev.run_id,
                    "symbol": ev.symbol,
                    "timeframe": ev.timeframe,
                    "ts": ev.ts,
                    "severity": ev.severity,
                    "code": ev.code,
                    "message": ev.message,
                }
                for ev in chunk
            ]
            result = conn.execute(data_quality_events_table.insert().values(rows))
            try:
                total += result.rowcount or 0
            except Exception:
                pass
    return total