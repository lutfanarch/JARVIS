"""Tests for the database-backed trade lock utilities.

These tests exercise the functions defined in ``informer.state.trade_lock``
to ensure that NY trading dates are computed correctly across DST
boundaries and that lock acquisition behaves atomically.  No
network access or external services are used.  A temporary SQLite
database is created for the duration of the tests.
"""

from __future__ import annotations

import datetime
from datetime import timezone, date
from pathlib import Path

import pytest

import sqlalchemy as sa

from informer.state.trade_lock import (
    get_ny_trading_date,
    try_acquire_lock,
    is_locked,
)
from informer.db.session import get_engine


def _create_trade_lock_table(engine: sa.engine.Engine) -> None:
    """Helper to create the trade_day_lock table in an ad-hoc SQLite database.

    The Alembic migration cannot be run directly in the test environment,
    so we reproduce the table definition here for testing purposes.
    """
    metadata = sa.MetaData()
    sa.Table(
        "trade_day_lock",
        metadata,
        sa.Column("ny_trading_date", sa.Date(), primary_key=True),
        sa.Column("locked_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("decision_hash", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=True),
    )
    metadata.create_all(engine)


def test_try_acquire_lock_atomicity(tmp_path: Path) -> None:
    """The first acquisition should succeed and the second should fail."""
    db_path = tmp_path / "lock.db"
    engine = get_engine(f"sqlite:///{db_path}")
    _create_trade_lock_table(engine)
    ny_date = date(2026, 1, 3)
    assert try_acquire_lock(engine, ny_date, "run1", "hash1", "AAPL") is True
    # Second attempt for the same date should return False
    assert try_acquire_lock(engine, ny_date, "run2", "hash2", "MSFT") is False
    # is_locked returns True after acquisition
    assert is_locked(engine, ny_date) is True
    # Another date remains unlocked
    assert is_locked(engine, date(2026, 1, 4)) is False


def test_get_ny_trading_date_dst() -> None:
    """NY trading date computation should match zoneinfo across DST boundaries."""
    from zoneinfo import ZoneInfo
    # A list of UTC datetimes around DST transitions in 2026
    test_datetimes = [
        # Before DST starts (NY observes EST)
        datetime.datetime(2026, 3, 6, 12, 0, tzinfo=timezone.utc),
        # Day of DST start (second Sunday in March)
        datetime.datetime(2026, 3, 8, 15, 0, tzinfo=timezone.utc),
        # Just after DST start
        datetime.datetime(2026, 3, 9, 2, 0, tzinfo=timezone.utc),
        # Before DST ends (NY observes EDT)
        datetime.datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc),
        # Day of DST end (first Sunday in November)
        datetime.datetime(2026, 11, 1, 15, 0, tzinfo=timezone.utc),
        # Just after DST end
        datetime.datetime(2026, 11, 2, 2, 0, tzinfo=timezone.utc),
    ]
    for dt in test_datetimes:
        expected_date = dt.astimezone(ZoneInfo("America/New_York")).date()
        assert get_ny_trading_date(dt) == expected_date