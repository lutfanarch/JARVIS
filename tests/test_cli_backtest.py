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
import json
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
    # Insert bars for multiple trading days to satisfy warmup (10 days * 26 = 260 bars)
    start_day = date(2025, 1, 2)
    days_inserted = 0
    current_day = start_day
    prices = [100 + i * 0.1 for i in range(26)]
    while days_inserted < 10:
        if current_day.weekday() < 5:
            bars = _make_bars_for_day(current_day, prices)
            with engine.begin() as conn:
                conn.execute(bars_table.insert(), bars)
            days_inserted += 1
        current_day = current_day + timedelta(days=1)
    # Prepare output directory
    out_dir = tmp_path / "out"
    runner = CliRunner()
    # Expand date range to cover all inserted days
    end_day = current_day - timedelta(days=1)
    result = runner.invoke(
        cli,
        [
            "backtest",
            "--start",
            start_day.isoformat(),
            "--end",
            end_day.isoformat(),
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


def test_cli_backtest_cost_params_persisted(tmp_path, monkeypatch):
    """Ensure that cost model parameters provided via CLI are persisted in summary.json.

    This test runs the backtest command with explicit slippage and commission
    settings and checks that these values are written under config.cost_model
    in the resulting summary JSON.
    """
    # Create temporary SQLite DB
    db_path = tmp_path / "bt.db"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    # Initialize DB and insert synthetic bars for multiple trading days
    engine = create_engine(db_url)
    metadata.create_all(engine)
    start_day = date(2025, 1, 2)
    days_inserted = 0
    current_day = start_day
    prices = [100 + i * 0.1 for i in range(26)]
    while days_inserted < 10:
        if current_day.weekday() < 5:
            bars = _make_bars_for_day(current_day, prices)
            with engine.begin() as conn:
                conn.execute(bars_table.insert(), bars)
            days_inserted += 1
        current_day = current_day + timedelta(days=1)
    # Define cost model parameters to test
    slippage_bps = 5.0
    commission_per_share = 0.02
    out_dir = tmp_path / "out2"
    runner = CliRunner()
    # Expand date range to include all inserted days
    end_day = current_day - timedelta(days=1)
    result = runner.invoke(
        cli,
        [
            "backtest",
            "--start",
            start_day.isoformat(),
            "--end",
            end_day.isoformat(),
            "--symbols",
            "AAPL",
            "--decision-time",
            "13:30",
            "--decision-tz",
            "America/New_York",
            "--out-dir",
            str(out_dir),
            "--slippage-bps",
            str(slippage_bps),
            "--commission-per-share",
            str(commission_per_share),
        ],
    )
    # CLI should exit successfully
    assert result.exit_code == 0, result.output
    # Ensure summary.json exists
    summary_path = out_dir / "summary.json"
    assert summary_path.exists(), f"summary.json not found at {summary_path}"
    # Load summary and verify cost model settings
    with summary_path.open() as f:
        summary_data = json.load(f)
    cfg = summary_data.get("config", {})
    cost_cfg = cfg.get("cost_model")
    assert cost_cfg is not None, "cost_model not present in summary config"
    assert cost_cfg.get("slippage_bps") == slippage_bps
    assert cost_cfg.get("commission_per_share") == commission_per_share