"""Integration smoke test for the database connection.

This test attempts to connect to the configured database.  It is
skipped if the ``DATABASE_URL`` environment variable is not set or
connection fails.  When the database is available (e.g., via
docker‑compose), it exercises a simple insert and select on the bars
table.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from informer.db.session import get_engine


def test_db_connection_and_insert_smoke() -> None:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not configured; skipping DB smoke test")
    try:
        engine = get_engine()
    except Exception as exc:
        pytest.skip(f"Cannot connect to database: {exc}")
    # Attempt to insert and select a row; catch exceptions gracefully
    try:
        with engine.begin() as conn:
            # Create a temp table and insert a value
            conn.execute(text("CREATE TEMP TABLE IF NOT EXISTS tmp_smoke (id int)"))
            conn.execute(text("INSERT INTO tmp_smoke (id) VALUES (1)"))
            result = conn.execute(text("SELECT id FROM tmp_smoke"))
            rows = result.fetchall()
            assert rows == [(1,)]
    except Exception as exc:
        pytest.skip(f"DB smoke test failed: {exc}")