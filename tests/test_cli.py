"""Tests for the Informer CLI ingest command."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from click.testing import CliRunner

from informer.cli import cli


def test_cli_rejects_non_whitelisted_symbol(monkeypatch) -> None:
    runner = CliRunner()
    # Provide dummy implementations so engine/provider don't error even though we won't reach them
    monkeypatch.setattr(
        "informer.cli._build_provider", lambda: None
    )
    monkeypatch.setattr(
        "informer.cli._build_engine", lambda: None
    )
    result = runner.invoke(
        cli,
        [
            "ingest",
            "--symbols",
            "INVALID",  # not in default halal universe (v2)
            "--start",
            "2025-01-01",
            "--end",
            "2025-01-02",
        ],
    )
    # The command should fail and mention the whitelist
    assert result.exit_code != 0
    assert "not in the allowed whitelist" in result.output


def test_cli_accepts_new_whitelisted_symbol(monkeypatch) -> None:
    """Ensure that a newly added symbol (e.g. TSLA) is accepted when present in the whitelist."""
    # Dummy provider returns no bars; asserts that the symbol list contains TSLA
    class DummyProvider:
        def get_historical_bars(self, symbols, timeframe, start, end):
            # symbols passed to the provider should match the request
            assert symbols == ["TSLA"]
            return []

    # Monkeypatch provider and engine builder
    monkeypatch.setattr("informer.cli._build_provider", lambda: DummyProvider())
    import sqlalchemy as sa
    monkeypatch.setattr("informer.cli._build_engine", lambda: sa.create_engine("sqlite:///:memory:"))
    # Patch upsert_bars to no-op to avoid DB writes
    monkeypatch.setattr("informer.ingestion.bars.upsert_bars", lambda *args, **kwargs: 0)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "ingest",
            "--symbols",
            "TSLA",
            "--timeframes",
            "15m",
            "--start",
            "2025-01-01",
            "--end",
            "2025-01-02",
        ],
    )
    # Exit code should be zero for success
    assert result.exit_code == 0


def test_cli_ingest_invokes_provider_and_upsert(monkeypatch) -> None:
    # Prepare dummy provider that returns no bars
    class DummyProvider:
        def get_historical_bars(self, symbols, timeframe, start, end):
            # Assert that timeframe arrives in lowercase canonical form
            assert timeframe == "15m"
            # Assert start and end are timezone aware
            assert start.tzinfo is not None
            assert end.tzinfo is not None
            return []

    # Monkeypatch the provider and engine builder
    monkeypatch.setattr(
        "informer.cli._build_provider", lambda: DummyProvider()
    )
    # Provide an in-memory SQLite engine
    import sqlalchemy as sa

    monkeypatch.setattr(
        "informer.cli._build_engine", lambda: sa.create_engine("sqlite:///:memory:")
    )
    # Patch upsert_bars to avoid actual DB operations and track calls
    calls = {"count": 0}

    def fake_upsert(engine, bars, chunk_size=2000):
        calls["count"] += 1
        return 0

    monkeypatch.setattr("informer.ingestion.bars.upsert_bars", fake_upsert)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "ingest",
            "--symbols",
            "AAPL",
            "--timeframes",
            "15m",
            "--start",
            "2025-01-01",
            "--end",
            "2025-01-02",
        ],
    )
    assert result.exit_code == 0
    # Should have printed a summary line
    assert "Ingested" in result.output
    # upsert_bars should have been called once (one timeframe)
    assert calls["count"] == 1


def test_cli_incremental_start(monkeypatch) -> None:
    """Regression test for incremental start calculation when --start is omitted.

    The CLI should compute the earliest start across requested symbols based
    on existing data in the database.  For a single symbol with a
    latest bar at 2025-01-01T12:00:00Z and timeframe 15m, the start
    should be 75 minutes earlier (5 bars * 15 minutes).  This test
    verifies that the provider receives the correct start parameter and
    that the CLI executes without error.
    """
    # Set up an in-memory SQLite engine and create the bars table
    import sqlalchemy as sa
    from informer.ingestion.bars import bars_table, metadata

    engine = sa.create_engine("sqlite:///:memory:")
    # Create only the bars table in SQLite
    metadata.create_all(engine, tables=[bars_table])
    # Insert a single bar row for AAPL
    ts_existing = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    with engine.begin() as conn:
        conn.execute(
            bars_table.insert().values(
                symbol="AAPL",
                timeframe="15m",
                ts=ts_existing,
                open=1.0,
                high=2.0,
                low=0.5,
                close=1.5,
                volume=100,
                vwap=1.25,
                source="test",
            )
        )
    # Prepare a dummy provider that records the start and end passed in
    calls = {
        "start": None,
        "end": None,
        "count": 0,
    }

    class DummyProvider:
        def get_historical_bars(self, symbols, timeframe, start, end):
            calls["start"] = start
            calls["end"] = end
            calls["count"] += 1
            return []

    # Monkeypatch provider and engine builder
    monkeypatch.setattr("informer.cli._build_provider", lambda: DummyProvider())
    monkeypatch.setattr("informer.cli._build_engine", lambda: engine)
    # Patch upsert_bars to no-op
    monkeypatch.setattr("informer.ingestion.bars.upsert_bars", lambda *args, **kwargs: 0)
    # Invoke CLI without start; provide end date
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "ingest",
            "--symbols",
            "AAPL",
            "--timeframes",
            "15m",
            "--end",
            "2025-01-02",
        ],
    )
    assert result.exit_code == 0
    # Provider should be called exactly once
    assert calls["count"] == 1
    # The computed start should be 75 minutes before the existing ts
    expected_start = ts_existing - timedelta(minutes=15 * 5)
    # Normalize to UTC and drop microseconds
    expected_start = expected_start.astimezone(timezone.utc).replace(microsecond=0)
    # Our CLI normalizes to UTC as well
    assert calls["start"].astimezone(timezone.utc).replace(microsecond=0) == expected_start