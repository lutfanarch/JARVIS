"""Corporate actions ingestion and upsert routines.

This module defines a SQLAlchemy table for corporate actions and
provides helper functions to idempotently upsert announcements
returned by data providers.  Actions are uniquely keyed by
``(symbol, action_type, ex_date)`` and subsequent ingestions will
update the payload rather than insert duplicates.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable, List, Tuple, Dict, Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert  # type: ignore
from sqlalchemy.dialects.sqlite import insert as sqlite_insert  # type: ignore
from sqlalchemy.engine import Engine

from ..providers.models import CorporateAction

# Define SQLAlchemy Core table for corporate actions.  We define a
# separate MetaData to avoid conflicts with other modules.  A unique
# constraint on (symbol, action_type, ex_date) enforces idempotent
# upserts.
metadata = sa.MetaData()
corporate_actions_table = sa.Table(
    "corporate_actions",
    metadata,
    sa.Column("symbol", sa.Text(), nullable=False),
    sa.Column("action_type", sa.Text(), nullable=False),
    sa.Column("ex_date", sa.Date(), nullable=False),
    sa.Column("payload_json", sa.JSON(), nullable=False),
    sa.Column(
        "ingested_at",
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.UniqueConstraint(
        "symbol", "action_type", "ex_date", name="uq_corporate_actions_symbol_action_type_ex_date"
    ),
)


def upsert_corporate_actions(
    engine: Engine,
    actions: List[CorporateAction] | List[Dict[str, Any]],
    chunk_size: int = 2000,
) -> int:
    """Insert or update corporate action records.

    Parameters
    ----------
    engine : sqlalchemy.engine.Engine
        Database engine used to execute the upsert.
    actions : list of CorporateAction or dict
        Actions to insert or update.  Dicts must contain keys
        ``symbol``, ``action_type``, ``ex_date`` and ``payload_json``, with
        optional ``source``.  For each action, the payload is cloned and
        ensured to contain a ``source`` key.
    chunk_size : int, optional
        Number of rows per execution chunk.  Defaults to 2000.

    Returns
    -------
    int
        Total number of rows affected across all chunks.  Note that
        rowcount semantics may vary by driver and dialect.
    """
    if not actions:
        return 0
    # Convert actions to row dicts
    rows: List[Dict[str, Any]] = []
    for act in actions:
        if isinstance(act, CorporateAction):
            payload = dict(act.payload_json)
            payload.setdefault("source", act.source)
            row = {
                "symbol": act.symbol,
                "action_type": act.action_type,
                "ex_date": act.ex_date,
                "payload_json": payload,
            }
        else:
            # Assume dict-like
            payload = dict(act.get("payload_json", {}))
            # Determine source: use provided or from act dict
            src = act.get("source") or payload.get("source")
            if src is not None:
                payload.setdefault("source", src)
            row = {
                "symbol": act.get("symbol"),
                "action_type": act.get("action_type"),
                "ex_date": act.get("ex_date"),
                "payload_json": payload,
            }
        rows.append(row)
    total_rowcount = 0
    # Choose insert function based on dialect
    dialect_name = engine.dialect.name
    # Map dialect to appropriate insert
    if dialect_name == "postgresql":
        insert_fn = pg_insert
    else:
        insert_fn = sqlite_insert
    # Execute in chunks
    with engine.begin() as conn:
        for i in range(0, len(rows), chunk_size):
            chunk = rows[i : i + chunk_size]
            stmt = insert_fn(corporate_actions_table).values(chunk)
            # Set up on conflict update for unique constraint
            update_dict = {
                "payload_json": stmt.excluded.payload_json,
            }
            stmt = stmt.on_conflict_do_update(
                index_elements=["symbol", "action_type", "ex_date"], set_=update_dict
            )
            result = conn.execute(stmt)
            try:
                total_rowcount += result.rowcount or 0
            except Exception:
                pass
    return total_rowcount


def ingest_corporate_actions(
    provider,  # type: ignore[type-arg]
    engine: Engine,
    symbols: List[str],
    start_date: date,
    end_date: date,
) -> Tuple[int, int]:
    """Fetch corporate actions from provider and upsert into DB.

    Parameters
    ----------
    provider : DataProvider
        Provider instance supporting get_corporate_actions.
    engine : sqlalchemy.engine.Engine
        Database engine to use for upsert.
    symbols : list of str
        Symbols to fetch corporate actions for.
    start_date : date
        Start date for corporate actions (inclusive).
    end_date : date
        End date for corporate actions (inclusive).

    Returns
    -------
    tuple
        (fetched_count, upserted_count)
    """
    # Fetch actions via provider
    actions = provider.get_corporate_actions(symbols=symbols, start=start_date, end=end_date)
    fetched_count = len(actions)
    upserted_count = upsert_corporate_actions(engine, actions)
    return (fetched_count, upserted_count)