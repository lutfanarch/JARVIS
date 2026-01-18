"""Tests for features snapshot storage upsert statements."""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from informer.features.storage import features_snapshot_table


def test_features_upsert_sql_contains_on_conflict() -> None:
    insert_stmt = sa.dialects.postgresql.insert(features_snapshot_table).values(
        [
            {
                "symbol": "AAPL",
                "timeframe": "15m",
                "ts": datetime(2025, 1, 1, tzinfo=timezone.utc),
                "indicators_json": {"ema20": 1.0},
                "patterns_json": {},
                "feature_version": "v0.1",
            }
        ]
    )
    upsert_stmt = insert_stmt.on_conflict_do_update(
        index_elements=["symbol", "timeframe", "ts", "feature_version"],
        set_={
            "indicators_json": insert_stmt.excluded.indicators_json,
            "patterns_json": insert_stmt.excluded.patterns_json,
        },
    )
    compiled = upsert_stmt.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "ON CONFLICT" in sql
    assert "DO UPDATE" in sql