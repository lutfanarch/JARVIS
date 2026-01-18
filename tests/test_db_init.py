"""Tests for database initialization on SQLite.

This module verifies that the ``jarvis db-init`` command correctly
applies Alembic migrations when the ``DATABASE_URL`` points to a
SQLite database.  The migration should create all required tables
without requiring TimescaleDB extensions.
"""

from __future__ import annotations

import os
from click.testing import CliRunner
import sqlalchemy as sa
from sqlalchemy import inspect

from informer.cli import cli


def test_db_init_creates_tables(tmp_path, monkeypatch) -> None:
    """Ensure that db-init creates all required tables on SQLite."""
    db_file = tmp_path / "jarvis_test.db"
    # Use a file-based SQLite URL for database initialization
    os.environ["DATABASE_URL"] = f"sqlite:///{db_file}"
    # Invoke the db-init command via Click
    runner = CliRunner()
    result = runner.invoke(cli, ["db-init"])
    # Command should exit successfully
    assert result.exit_code == 0, result.output
    # Connect to the database and inspect the created tables
    engine = sa.create_engine(os.environ["DATABASE_URL"])
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    required = {"bars", "data_quality_events", "corporate_actions", "features_snapshot", "alembic_version"}
    assert required.issubset(tables)