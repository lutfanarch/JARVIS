"""Tests for the Informer QA CLI command."""

from __future__ import annotations

from datetime import datetime, timezone

from click.testing import CliRunner
import sqlalchemy as sa

from informer.cli import cli
from informer.ingestion.bars import bars_table, metadata as bars_metadata
from informer.quality.storage import data_quality_events_table, metadata as dq_metadata


def test_cli_qa_inserts_events(monkeypatch) -> None:
    """Run the qa command and ensure it inserts at least one quality event."""
    # Create an in-memory SQLite engine
    engine = sa.create_engine("sqlite:///:memory:")
    # Create bars and data_quality_events tables
    bars_metadata.create_all(engine, tables=[bars_table])
    dq_metadata.create_all(engine, tables=[data_quality_events_table])
    # Insert a bar with invalid OHLC to trigger an error
    ts = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    with engine.begin() as conn:
        conn.execute(
            bars_table.insert().values(
                symbol="AAPL",
                timeframe="15m",
                ts=ts,
                open=-1.0,  # invalid
                high=2.0,
                low=0.5,
                close=1.5,
                volume=100,
                vwap=1.25,
                source="test",
            )
        )
    # Monkeypatch the engine builder to return our sqlite engine
    monkeypatch.setattr("informer.cli._build_engine", lambda: engine)
    # Run qa command
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "qa",
            "--symbols",
            "AAPL",
            "--timeframes",
            "15m",
            "--start",
            "2025-01-01",
            "--end",
            "2025-01-02",
            "--run-id",
            "test_run",
        ],
    )
    # Command should succeed
    assert result.exit_code == 0
    # Verify events inserted
    with engine.connect() as conn:
        result_rows = conn.execute(data_quality_events_table.select()).fetchall()
        assert len(result_rows) >= 1