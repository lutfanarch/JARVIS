"""Tests for the Informer healthcheck CLI command."""

from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa
from click.testing import CliRunner

from informer.cli import cli
from informer.ingestion.bars import bars_table, metadata as bars_metadata
from informer.features.storage import features_snapshot_table, metadata as features_metadata
from informer.quality.storage import data_quality_events_table, metadata as quality_metadata
from informer.ingestion.corporate_actions import corporate_actions_table, metadata as ca_metadata


def _create_alembic_version_table(engine: sa.engine.Engine) -> None:
    """Create a minimal alembic_version table for tests.

    The health check expects this table to exist.  This helper
    constructs a simple version table compatible with SQLite.
    """
    from sqlalchemy import Table, Column, MetaData, Text

    metadata = MetaData()
    version_table = Table(
        "alembic_version", metadata, Column("version_num", Text, nullable=False)
    )
    metadata.create_all(engine)


def _setup_schema(engine: sa.engine.Engine, create_all: bool = True) -> None:
    """Create the required database tables for the healthcheck.

    When ``create_all`` is True, all core tables will be created.  If
    False, only a subset is created to simulate missing schema.
    """
    # Always create the bars table and alembic_version
    bars_metadata.create_all(engine, tables=[bars_table])
    _create_alembic_version_table(engine)
    if create_all:
        # Create the remaining required tables
        features_metadata.create_all(engine, tables=[features_snapshot_table])
        quality_metadata.create_all(engine, tables=[data_quality_events_table])
        ca_metadata.create_all(engine, tables=[corporate_actions_table])


def test_cli_healthcheck_json_ok(monkeypatch, tmp_path) -> None:
    """Invoke the healthcheck CLI and verify that a JSON report is printed and written to disk.

    This test constructs an in-memory SQLite database with all required
    tables, patches the engine builder to return this engine and runs
    the healthcheck command with an explicit run_id and output path.
    It asserts that the command exits successfully, writes the report
    file and that the JSON report (both printed and on disk) contains
    the expected top-level keys.
    """
    # Prepare in-memory SQLite engine and create all required tables
    engine = sa.create_engine("sqlite:///:memory:")
    _setup_schema(engine, create_all=True)
    # Monkeypatch engine builder
    monkeypatch.setattr("informer.cli._build_engine", lambda: engine)
    # Set DATABASE_URL so ENV_DATABASE_URL check passes
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    # Clear Alpaca keys to generate a WARN but not fail
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
    # Define run id and output path
    run_id = "testrun"
    out_path = tmp_path / "r.json"
    # Run CLI with --out and --json
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "healthcheck",
            "--symbols",
            "AAPL",
            "--timeframes",
            "15m",
            "--run-id",
            run_id,
            "--out",
            str(out_path),
            "--artifacts-root",
            str(tmp_path),
            "--json",
        ],
    )
    # The command should exit successfully
    assert result.exit_code == 0, result.output
    # The report file should exist
    assert out_path.exists(), f"Report file {out_path} was not created"
    # Load report from file and assert basic structure
    with out_path.open("r", encoding="utf-8") as f:
        file_report = json.load(f)
    for key in ["run_id", "generated_at", "status", "checks", "versions", "environment"]:
        assert key in file_report
    # Verify printed JSON contains the same keys (the last JSON in output)
    out = result.output.strip()
    json_start = out.find("{")
    assert json_start != -1, "JSON report not found in output"
    printed_report = json.loads(out[json_start:])
    for key in ["run_id", "generated_at", "status", "checks", "versions", "environment"]:
        assert key in printed_report


def test_cli_healthcheck_not_ready_on_missing_tables(monkeypatch, tmp_path) -> None:
    """Healthcheck CLI should report NOT_READY when schema tables are missing.

    This test intentionally omits creation of some required tables to
    induce an error.  It verifies that the healthcheck human summary
    includes a NOT_READY status and that the command still exits
    successfully (exit code 0).
    """
    engine = sa.create_engine("sqlite:///:memory:")
    # Create only bars and alembic_version tables
    _setup_schema(engine, create_all=False)
    # Monkeypatch engine builder
    monkeypatch.setattr("informer.cli._build_engine", lambda: engine)
    # Set DB URL
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "healthcheck",
            "--symbols",
            "AAPL",
            "--timeframes",
            "15m",
            "--artifacts-root",
            str(tmp_path),
        ],
    )
    # Should still exit normally
    assert result.exit_code == 0, result.output
    # Summary line should indicate NOT_READY
    assert "status=NOT_READY" in result.output


def test_cli_healthcheck_strict_exit(monkeypatch, tmp_path) -> None:
    """healthcheck should exit with code 2 in strict mode when DATABASE_URL is missing.

    This test verifies that when the database URL is not set, the healthcheck
    does not attempt to build an engine, reports NOT_READY and exits with
    status code 2 under --strict.
    """
    # Ensure DATABASE_URL is unset
    monkeypatch.delenv("DATABASE_URL", raising=False)
    # Patch _build_engine to raise if called (should not be called when DB URL missing)
    def raise_build():
        raise AssertionError("_build_engine should not be called when DATABASE_URL is missing")

    monkeypatch.setattr("informer.cli._build_engine", raise_build)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "healthcheck",
            "--symbols",
            "AAPL",
            "--strict",
        ],
    )
    # Exit code 2 expected in strict mode when NOT_READY
    assert result.exit_code == 2, result.output
    assert "status=NOT_READY" in result.output