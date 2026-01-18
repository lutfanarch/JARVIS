"""Persistence for features snapshots.

This module defines the SQLAlchemy table for the ``features_snapshot``
and provides an upsert helper that supports both PostgreSQL and SQLite
dialects.  The table stores computed indicator snapshots for each
symbol, timeframe and timestamp along with a feature version.
"""

from __future__ import annotations

from typing import Iterable, List, Dict

from sqlalchemy import Column, MetaData, Table, Text, TIMESTAMP, JSON, PrimaryKeyConstraint
from sqlalchemy.engine import Engine
from sqlalchemy.dialects import postgresql, sqlite

# Define metadata and table.  A primary key ensures deterministic
# conflict handling in SQLite tests.
metadata = MetaData()
features_snapshot_table = Table(
    "features_snapshot",
    metadata,
    Column("symbol", Text, nullable=False),
    Column("timeframe", Text, nullable=False),
    Column("ts", TIMESTAMP(timezone=True), nullable=False),
    Column("indicators_json", JSON, nullable=False),
    Column("patterns_json", JSON, nullable=False),
    Column("feature_version", Text, nullable=False),
    PrimaryKeyConstraint(
        "symbol",
        "timeframe",
        "ts",
        "feature_version",
        name="pk_features_snapshot",
    ),
)


def upsert_features_snapshot(
    engine: Engine, rows: Iterable[Dict], chunk_size: int = 2000
) -> int:
    """Insert or update feature snapshot rows into the database.

    Args:
        engine: SQLAlchemy engine bound to the target database.
        rows: Iterable of dictionaries with keys matching the table columns:
            symbol, timeframe, ts, indicators_json, patterns_json, feature_version.
        chunk_size: Maximum number of rows per batch.

    Returns:
        The number of rows affected (best effort).
    """
    rows_list = list(rows)
    if not rows_list:
        return 0
    total = 0
    dialect_name = engine.dialect.name
    with engine.begin() as conn:
        for i in range(0, len(rows_list), chunk_size):
            chunk = rows_list[i : i + chunk_size]
            # Determine appropriate insert statement based on dialect
            if dialect_name == "postgresql":
                insert_stmt = postgresql.insert(features_snapshot_table).values(chunk)
                update_dict = {
                    "indicators_json": insert_stmt.excluded.indicators_json,
                    "patterns_json": insert_stmt.excluded.patterns_json,
                }
                stmt = insert_stmt.on_conflict_do_update(
                    index_elements=[
                        "symbol",
                        "timeframe",
                        "ts",
                        "feature_version",
                    ],
                    set_=update_dict,
                )
            elif dialect_name == "sqlite":
                insert_stmt = sqlite.insert(features_snapshot_table).values(chunk)
                stmt = insert_stmt.on_conflict_do_update(
                    index_elements=[
                        "symbol",
                        "timeframe",
                        "ts",
                        "feature_version",
                    ],
                    set_={
                        "indicators_json": insert_stmt.excluded.indicators_json,
                        "patterns_json": insert_stmt.excluded.patterns_json,
                    },
                )
            else:
                # Fallback: try to use generic insert; conflicts not handled
                stmt = features_snapshot_table.insert().values(chunk)
            result = conn.execute(stmt)
            try:
                total += result.rowcount or 0
            except Exception:
                pass
    return total