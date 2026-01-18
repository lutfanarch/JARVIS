"""Database-backed trade lock utilities.

This module implements a persistent lock to enforce the rule that at
most one trade may be executed on any given America/New_York trading
date.  The lock is stored in a relational database table created via
Alembic (see revision ``0003_create_trade_day_lock``).  Functions
provided here allow callers to compute the NY trading date from a
UTC timestamp, check if a lock exists, and attempt to acquire a
lock atomically.  Optional helpers expose lock details and allow
clearing a lock (for operator debugging).  These functions
operate on a passed :class:`sqlalchemy.engine.Engine` and are
compatible with both SQLite and PostgreSQL.
"""

from __future__ import annotations

from datetime import datetime, date, timezone
from typing import Optional, Tuple, Any

from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError


def get_ny_trading_date(as_of_utc: datetime) -> date:
    """Return the America/New_York calendar date for a UTC timestamp.

    The input datetime must be timezone-aware.  The returned date
    corresponds to the civil date in New York at the given moment.
    """
    if as_of_utc.tzinfo is None:
        # Assume UTC when no tzinfo is present
        as_of_utc = as_of_utc.replace(tzinfo=timezone.utc)
    dt_ny = as_of_utc.astimezone(ZoneInfo("America/New_York"))
    return dt_ny.date()


def is_locked(engine: Engine, ny_date: date) -> bool:
    """Return True if a trade lock exists for the given NY trading date.

    This performs a simple existence query against the
    ``trade_day_lock`` table.  Any exception is treated as unlocked,
    allowing callers to proceed when the table does not exist.

    Args:
        engine: A SQLAlchemy engine bound to the target database.
        ny_date: The NY trading date to check.

    Returns:
        True if a row exists for ``ny_date``, False otherwise.
    """
    try:
        with engine.connect() as conn:
            result = conn.execute(
                sa.text(
                    "SELECT 1 FROM trade_day_lock WHERE ny_trading_date = :d"
                ),
                {"d": ny_date},
            ).fetchone()
            return result is not None
    except Exception:
        # If the table does not exist or any other error occurs,
        # conservatively report that no lock is present.
        return False


def try_acquire_lock(
    engine: Engine,
    ny_date: date,
    run_id: str,
    decision_hash: str,
    symbol: Optional[str],
) -> bool:
    """Attempt to acquire a trade lock for the given NY trading date.

    An insertion is performed into the ``trade_day_lock`` table.  If
    the ``ny_trading_date`` is already present (due to the primary
    key), the function returns ``False`` without raising.  On any
    other error (e.g., missing table) the operation is aborted and
    ``False`` is returned.

    Args:
        engine: SQLAlchemy engine bound to the database.
        ny_date: The NY trading date for which to acquire the lock.
        run_id: Identifier of the run attempting the trade.
        decision_hash: Deterministic hash of the decision payload.
        symbol: The trade symbol, or None when no symbol is involved.

    Returns:
        True if the lock was acquired, False if it already exists or an
        error occurred.
    """
    locked_at = datetime.now(timezone.utc)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO trade_day_lock (ny_trading_date, locked_at_utc, run_id, decision_hash, symbol) "
                    "VALUES (:d, :ts, :rid, :hash, :sym)"
                ),
                {"d": ny_date, "ts": locked_at, "rid": run_id, "hash": decision_hash, "sym": symbol},
            )
        return True
    except IntegrityError:
        # Already locked
        return False
    except Exception:
        # On missing table or other failure treat as failed acquisition
        return False


def get_lock_details(engine: Engine, ny_date: date) -> Optional[Tuple[Any, ...]]:
    """Return details about a lock for the given NY date.

    The returned tuple contains the columns
    ``(ny_trading_date, run_id, decision_hash, symbol, locked_at_utc)``
    or ``None`` if no lock exists or the table is missing.

    Args:
        engine: SQLAlchemy engine bound to the database.
        ny_date: The NY trading date to inspect.

    Returns:
        A tuple with lock details or ``None``.
    """
    try:
        with engine.connect() as conn:
            row = conn.execute(
                sa.text(
                    "SELECT ny_trading_date, run_id, decision_hash, symbol, locked_at_utc "
                    "FROM trade_day_lock WHERE ny_trading_date = :d"
                ),
                {"d": ny_date},
            ).fetchone()
            return row
    except Exception:
        return None


def clear_lock(engine: Engine, ny_date: date) -> None:
    """Remove the lock for a NY trading date, if present.

    This helper is intended for operator/testing use.  It silently
    returns even if the table or row does not exist.
    """
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text("DELETE FROM trade_day_lock WHERE ny_trading_date = :d"),
                {"d": ny_date},
            )
    except Exception:
        # Ignore failures
        pass