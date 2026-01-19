"""Tests for pre‑start warmup loading in the backtest CLI.

These tests verify that the backtest command automatically loads
enough history prior to the requested start date to satisfy the
warmup threshold.  When sufficient bars exist before the start
date, the backtest should be able to trade on the first day of the
requested range without requiring the operator to extend the range
manually.  When history remains insufficient, warmup gating should
still block trading and record a reason.
"""

from datetime import datetime, date, time, timedelta
from typing import List, Dict, Any, Iterable

import pytest
from click.testing import CliRunner
from sqlalchemy import create_engine

from informer.cli import cli
from informer.ingestion.bars import metadata, bars_table  # to create schema


def _make_rth_bars_for_day(trading_day: date, prices: List[float]) -> List[Dict[str, Any]]:
    """Build synthetic RTH 15m bars for a single trading day.

    Bars start at 09:30 America/New_York and increment by 15 minutes.
    Prices list defines the close for each bar; open/high/low are
    derived deterministically around the close.  Timestamps are
    converted to UTC.  The returned dicts include the keys required
    by the ingestion schema: symbol, timeframe, ts, open, high, low,
    close, volume, vwap, source.
    """
    from zoneinfo import ZoneInfo

    bars = []
    base_time = datetime.combine(trading_day, time(9, 30)).replace(
        tzinfo=ZoneInfo("America/New_York")
    )
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


def _stub_indicators(bars: Iterable[Dict[str, Any]], timeframe: str) -> List[Dict[str, Any]]:
    """Deterministic indicator stub.

    Ignores the input bar values and returns fixed indicator values
    aligned to the length of the input sequence.  The values are
    chosen so that the baseline strategy will generate trades when
    warmup is satisfied.
    """
    out: List[Dict[str, Any]] = []
    for b in bars:
        ts = b.get("ts") if isinstance(b, dict) else getattr(b, "ts", None)
        close = b.get("close") if isinstance(b, dict) else getattr(b, "close", None)
        out.append(
            {
                "ts": ts,
                "ema20": 2.0,
                "ema50": 1.0,
                "ema200": 1.0,
                "rsi14": 50.0,
                "atr14": 1.0,
                "vwap": close if close is not None else 0.0,
            }
        )
    return out


def _stub_regimes(
    bars: Iterable[Dict[str, Any]], indicators: List[Dict[str, Any]], timeframe: str
) -> List[Dict[str, Any]]:
    """Deterministic regime stub.

    Produces a list of regime labels aligned with the indicator list.
    Always returns ``uptrend`` for the 1h trend regime and ``low`` for
    the 15m volatility regime so that the baseline strategy accepts
    candidate trades when warmup and score thresholds are met.
    """
    out: List[Dict[str, Any]] = []
    n = min(len(bars), len(indicators))
    for i in range(n):
        ts = indicators[i].get("ts")
        out.append({"ts": ts, "trend_regime": "uptrend", "vol_regime": "low"})
    return out


def test_backtest_prestarthistory_trades_on_start_day(tmp_path, monkeypatch):
    """Backtest should trade on the first day when sufficient pre‑start history exists."""
    # Monkeypatch indicator and regime computations to deterministic stubs
    monkeypatch.setattr(
        "informer.backtest.strategy.compute_indicators", _stub_indicators
    )
    monkeypatch.setattr(
        "informer.backtest.strategy.compute_regimes", _stub_regimes
    )
    # Setup temporary SQLite database
    db_path = tmp_path / "prestarthist.db"
    db_url = f"sqlite:///{db_path}"
    engine = create_engine(db_url)
    metadata.create_all(engine)
    # Insert 10 trading days of bars starting on a Monday
    start_day = date(2025, 1, 6)  # Monday
    days_inserted = 0
    cur_day = start_day
    last_day_inserted = start_day
    prices = [100 + i * 0.1 for i in range(26)]
    while days_inserted < 10:
        if cur_day.weekday() < 5:  # weekday
            bars = _make_rth_bars_for_day(cur_day, prices)
            with engine.begin() as conn:
                conn.execute(bars_table.insert(), bars)
            days_inserted += 1
            last_day_inserted = cur_day
        cur_day = cur_day + timedelta(days=1)
    engine.dispose()
    # The backtest start_date is the last inserted trading day
    start_date = last_day_inserted
    # Set DATABASE_URL so the CLI uses our temporary database
    monkeypatch.setenv("DATABASE_URL", db_url)
    # Run backtest via CLI for a single day
    runner = CliRunner()
    out_dir = tmp_path / "prestarthist_out"
    result = runner.invoke(
        cli,
        [
            "backtest",
            "--start",
            start_date.isoformat(),
            "--end",
            start_date.isoformat(),
            "--symbols",
            "AAPL",
            "--decision-time",
            "10:15",
            "--decision-tz",
            "America/New_York",
            "--out-dir",
            str(out_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    # Load trades.csv and reasons.csv
    trades_csv = out_dir / "trades.csv"
    reasons_csv = out_dir / "reasons.csv"
    assert trades_csv.exists(), "trades.csv missing"
    assert reasons_csv.exists(), "reasons.csv missing"
    # trades.csv should have at least one trade row (header + >=1)
    with trades_csv.open() as f:
        lines = [line.strip() for line in f.readlines() if line.strip()]
    assert len(lines) > 1, f"Expected trades on start day, got lines: {lines}"
    # Verify that all trade dates equal the requested start_date
    for row in lines[1:]:
        cols = row.split(",")
        # Second column is date (YYYY-MM-DD)
        tr_date = cols[1]
        assert tr_date == start_date.isoformat(), f"Trade date {tr_date} not equal to start date {start_date.isoformat()}"


def test_backtest_prestarthistory_blocks_when_insufficient(tmp_path, monkeypatch):
    """Backtest should block trading when pre‑start history is insufficient."""
    # Monkeypatch indicator and regime computations to deterministic stubs
    monkeypatch.setattr(
        "informer.backtest.strategy.compute_indicators", _stub_indicators
    )
    monkeypatch.setattr(
        "informer.backtest.strategy.compute_regimes", _stub_regimes
    )
    # Setup temporary SQLite database
    db_path = tmp_path / "prestarthist2.db"
    db_url = f"sqlite:///{db_path}"
    engine = create_engine(db_url)
    metadata.create_all(engine)
    # Insert only 3 trading days of bars starting on a Monday
    start_day = date(2025, 1, 6)  # Monday
    days_inserted = 0
    cur_day = start_day
    last_day_inserted = start_day
    prices = [100 + i * 0.1 for i in range(26)]
    while days_inserted < 3:
        if cur_day.weekday() < 5:
            bars = _make_rth_bars_for_day(cur_day, prices)
            with engine.begin() as conn:
                conn.execute(bars_table.insert(), bars)
            days_inserted += 1
            last_day_inserted = cur_day
        cur_day = cur_day + timedelta(days=1)
    engine.dispose()
    # Backtest start_date is the last inserted trading day
    start_date = last_day_inserted
    monkeypatch.setenv("DATABASE_URL", db_url)
    runner = CliRunner()
    out_dir = tmp_path / "prestarthist2_out"
    result = runner.invoke(
        cli,
        [
            "backtest",
            "--start",
            start_date.isoformat(),
            "--end",
            start_date.isoformat(),
            "--symbols",
            "AAPL",
            "--decision-time",
            "10:15",
            "--decision-tz",
            "America/New_York",
            "--out-dir",
            str(out_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    trades_csv = out_dir / "trades.csv"
    reasons_csv = out_dir / "reasons.csv"
    assert trades_csv.exists(), "trades.csv missing"
    assert reasons_csv.exists(), "reasons.csv missing"
    # trades.csv should only contain header (no trades)
    with trades_csv.open() as f:
        lines = [line.strip() for line in f.readlines() if line.strip()]
    assert len(lines) == 1, f"Expected no trades, got lines: {lines}"
    # reasons.csv should include warmup reason
    with reasons_csv.open() as f:
        reasons_lines = [line.strip() for line in f.readlines() if line.strip()]
    # Header + at least one reason row
    assert len(reasons_lines) >= 2, "reasons.csv should contain header and at least one reason"
    has_warmup = any("WARMUP_INSUFFICIENT_BARS" in row for row in reasons_lines[1:])
    assert has_warmup, f"Expected WARMUP_INSUFFICIENT_BARS reason, got: {reasons_lines}"