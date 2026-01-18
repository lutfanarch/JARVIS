"""Tests for chart rendering functions and CLI command."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import sqlalchemy as sa
from click.testing import CliRunner

from informer.cli import cli
from informer.ingestion.bars import bars_table, metadata as bars_metadata
from informer.charts.renderer import render_chart_for_symbol_timeframe, CHART_VERSION_DEFAULT


def _insert_synthetic_bars(engine: sa.engine.Engine, symbol: str, timeframe: str, start_ts: datetime, count: int, interval_minutes: int = 15) -> None:
    """Insert synthetic bars into the bars table for testing.

    Each bar will have increasing timestamps by ``interval_minutes`` minutes and
    deterministic OHLCV values.
    """
    rows = []
    for i in range(count):
        ts = start_ts + timedelta(minutes=interval_minutes * i)
        rows.append(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "ts": ts,
                "open": float(i) + 1.0,
                "high": float(i) + 2.0,
                "low": float(i) + 0.5,
                "close": float(i) + 1.5,
                "volume": 100 + i,
                "vwap": None,
                "source": "test",
            }
        )
    with engine.begin() as conn:
        conn.execute(bars_table.insert().values(rows))


def test_render_chart_writes_png(tmp_path) -> None:
    """render_chart_for_symbol_timeframe writes a PNG file for valid data."""
    engine = sa.create_engine("sqlite:///:memory:")
    # Create tables
    bars_metadata.create_all(engine, tables=[bars_table])
    # Insert synthetic bars
    base_ts = datetime(2025, 1, 1, 9, 30, tzinfo=timezone.utc)
    _insert_synthetic_bars(engine, "AAPL", "15m", base_ts, count=60, interval_minutes=15)
    # Define start/end covering inserted bars
    start_dt = base_ts
    end_dt = base_ts + timedelta(minutes=15 * 60)
    out_dir = tmp_path
    chart_path = render_chart_for_symbol_timeframe(
        engine=engine,
        symbol="AAPL",
        timeframe="15m",
        start=start_dt,
        end=end_dt,
        out_dir=out_dir,
        chart_version=CHART_VERSION_DEFAULT,
        limit_bars=200,
    )
    assert chart_path is not None, "Expected a chart path to be returned"
    # File should exist and be a PNG
    assert chart_path.is_file(), f"File {chart_path} does not exist"
    with open(chart_path, "rb") as f:
        header = f.read(8)
    assert header == b"\x89PNG\r\n\x1a\n", "Output file is not a valid PNG"


def test_charts_cli_creates_files(tmp_path, monkeypatch) -> None:
    """The charts CLI should create chart files and report their creation."""
    engine = sa.create_engine("sqlite:///:memory:")
    # Create tables
    bars_metadata.create_all(engine, tables=[bars_table])
    # Insert synthetic bars for AAPL 15m
    base_ts = datetime(2025, 1, 1, 9, 30, tzinfo=timezone.utc)
    _insert_synthetic_bars(engine, "AAPL", "15m", base_ts, count=40, interval_minutes=15)
    # Monkeypatch engine builder
    monkeypatch.setattr("informer.cli._build_engine", lambda: engine)
    runner = CliRunner()
    # Run charts command
    result = runner.invoke(
        cli,
        [
            "charts",
            "--symbols",
            "AAPL",
            "--timeframes",
            "15m",
            "--start",
            "2025-01-01",
            "--end",
            "2025-01-02",
            "--out-dir",
            str(tmp_path),
            "--chart-version",
            CHART_VERSION_DEFAULT,
            "--limit",
            "100",
        ],
    )
    assert result.exit_code == 0, result.output
    # Check file path under version/symbol/timeframe
    chart_dir = tmp_path / CHART_VERSION_DEFAULT / "AAPL"
    expected_path = chart_dir / "15m.png"
    assert expected_path.is_file(), f"Expected chart file {expected_path} to exist"