"""Test the CLI backtest command end-to-end using synthetic bar data.

This test verifies that the backtest CLI runs successfully when provided
with a SQLite database containing synthetic 15‑minute bars.  It asserts
that the command exits without error, writes all expected artifact files,
and produces at least one trade row in the trades CSV.
"""

from datetime import datetime, date, time, timedelta
import os
from typing import List

import pytest
from click.testing import CliRunner

from informer.cli import cli
from informer.ingestion.bars import metadata, bars_table  # to create schema
from sqlalchemy import create_engine


def _make_bars_for_day(trading_day: date, prices: List[float]) -> List[dict]:
    """Helper to build 15m bar dicts with UTC timestamps."""
    from zoneinfo import ZoneInfo
    bars = []
    base_time = datetime.combine(trading_day, time(9, 30)).replace(tzinfo=ZoneInfo("America/New_York"))
    for i, price in enumerate(prices):
        ts_local = base_time + timedelta(minutes=15 * i)
        ts_utc = ts_local.astimezone(ZoneInfo("UTC"))
        bars.append(
            {
                "symbol": "AAPL",
                "timeframe": "15m",
                "ts": ts_utc,
                "open": price,
                "high": price + 0.5,
                "low": price - 0.5,
                "close": price,
                "volume": 1000,
                "vwap": price,
                "source": "TEST",
            }
        )
    return bars


def test_cli_backtest_runs_and_produces_artifacts(tmp_path, monkeypatch):
    """Run the backtest CLI against synthetic data and verify artifacts."""
    # Create a temporary SQLite database file
    db_path = tmp_path / "bt.db"
    db_url = f"sqlite:///{db_path}"
    # Monkeypatch DATABASE_URL so CLI uses this SQLite file
    monkeypatch.setenv("DATABASE_URL", db_url)
    # Initialize the database and create the bars table
    engine = create_engine(db_url)
    metadata.create_all(engine)
    # Insert a full day of bars: 26 bars from 09:30 to 16:00 ET inclusive
    # The baseline strategy needs enough bars for ATR/indicators.
    prices = [100 + i * 0.1 for i in range(26)]
    bars = _make_bars_for_day(date(2025, 1, 2), prices)
    with engine.begin() as conn:
        conn.execute(bars_table.insert(), bars)
    # Prepare output directory
    out_dir = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "backtest",
            "--start",
            "2025-01-02",
            "--end",
            "2025-01-02",
            "--symbols",
            "AAPL",
            "--decision-time",
            "13:30",
            "--decision-tz",
            "America/New_York",
            "--out-dir",
            str(out_dir),
        ],
    )
    # Command should exit successfully
    assert result.exit_code == 0, result.output
    # Expect output directory exists
    assert out_dir.exists() and out_dir.is_dir()
    # Check artifact files
    summary_path = out_dir / "summary.json"
    trades_path = out_dir / "trades.csv"
    eq_path = out_dir / "equity_curve.csv"
    reasons_path = out_dir / "reasons.csv"
    assert summary_path.exists()
    assert trades_path.exists()
    assert eq_path.exists()
    assert reasons_path.exists()
    # Check that the trades CSV has at least one data row (besides header)
    with trades_path.open() as f:
        lines = [line.strip() for line in f.readlines()]
    # At least two lines: header + one trade row
    assert len(lines) >= 2, f"No trades found in trades.csv: {lines}"