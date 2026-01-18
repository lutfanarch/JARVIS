"""Tests for the bar upsert logic.

These tests build the SQLAlchemy upsert statement without executing it
against a real database.  They assert that the generated SQL contains
the expected ``ON CONFLICT`` clause and update assignments.
"""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from informer.providers.models import Bar
from informer.ingestion.bars import bars_table


def test_upsert_statement_contains_on_conflict() -> None:
    # Build a sample bar
    bar = Bar(
        symbol="AAPL",
        timeframe="15m",
        ts=datetime(2023, 1, 1, 10, 0, tzinfo=timezone.utc),
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=100,
        vwap=1.25,
        source="alpaca",
    )
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
    insert_stmt = sa.dialects.postgresql.insert(bars_table).values([row])
    update_dict = {
        "open": insert_stmt.excluded.open,
        "high": insert_stmt.excluded.high,
        "low": insert_stmt.excluded.low,
        "close": insert_stmt.excluded.close,
        "volume": insert_stmt.excluded.volume,
        "vwap": insert_stmt.excluded.vwap,
        "source": insert_stmt.excluded.source,
    }
    upsert_stmt = insert_stmt.on_conflict_do_update(
        index_elements=["symbol", "timeframe", "ts"], set_=update_dict
    )
    compiled = upsert_stmt.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "ON CONFLICT" in sql
    assert "DO UPDATE" in sql
    # Ensure that the columns to update are present
    assert "high" in sql and "low" in sql and "vwap" in sql