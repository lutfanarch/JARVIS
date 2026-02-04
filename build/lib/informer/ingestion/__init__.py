"""Data ingestion utilities.

This package contains functions for fetching market data via a provider
and inserting it into the Informer's database.  The ingestion API
exposes idempotent upsert routines built on top of SQLAlchemy Core
so that repeated runs do not create duplicate rows.
"""

from __future__ import annotations

from .bars import upsert_bars, ingest_timeframe

__all__ = [
    "upsert_bars",
    "ingest_timeframe",
]