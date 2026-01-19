"""Tests for backtest warmup semantics.

This module verifies that the backtest engine enforces a minimum number
of bars before generating trade candidates.  When the total number of
15‑minute bars available up to the decision time is below the warmup
threshold, the engine should produce no trades and record a
``WARMUP_INSUFFICIENT_BARS`` reason.  Once the threshold is met, trades
should proceed normally (under deterministic bullish stubs).
"""

from datetime import datetime, date, time, timedelta
import json
from typing import List, Dict, Iterable, Any

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
    converted to UTC.
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


def _stub_indicators(
    bars: Iterable[Dict[str, Any]], timeframe: str
) -> List[Dict[str, Any]]:
    """Return deterministic indicator values for each bar.

    This stub ignores the actual bar data and returns a list of dicts
    matching the length of the input.  Each dict includes fields used
    by the baseline strategy: ema20, ema50, ema200, rsi14, atr14 and vwap.
    The values are chosen so that the baseline strategy produces a
    positive score and thus generates trades when score_threshold <= 1.0.
    """
    out = []
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
    """Return deterministic regime labels for each bar.

    The baseline strategy uses the last element of this list to check
    ``trend_regime`` for 1h bars and ``vol_regime`` for 15m bars.  By
    returning ``uptrend`` and ``low`` respectively, the strategy will
    accept the candidate trade when combined with the stubbed
    indicators.
    """
    out = []
    it_bars = list(bars)
    n = min(len(it_bars), len(indicators))
    for i in range(n):
        ts = indicators[i].get("ts")
        out.append({"ts": ts, "trend_regime": "uptrend", "vol_regime": "low"})
    return out


def test_backtest_warmup_blocks_until_threshold(tmp_path, monkeypatch):
    """Backtest should not trade before the warmup threshold of 200 bars is reached."""
    # Patch indicator and regime functions to deterministic stubs.  Patch on
    # informer.backtest.strategy so that the baseline strategy uses these
    # stubs rather than the real implementations.  The wrappers in
    # informer.backtest.strategy forward to the underlying features
    # functions; monkeypatching here ensures the baseline strategy sees
    # the stubbed behaviour.
    monkeypatch.setattr(
        "informer.backtest.strategy.compute_indicators", _stub_indicators
    )
    monkeypatch.setattr(
        "informer.backtest.strategy.compute_regimes", _stub_regimes
    )
    # Setup temporary SQLite database
    db_path = tmp_path / "warmup.db"
    db_url = f"sqlite:///{db_path}"
    engine = create_engine(db_url)
    metadata.create_all(engine)
    # Insert bars for 3 trading days (3*26 = 78 bars < 200)
    start_day = date(2025, 1, 6)  # Monday
    days_inserted = 0
    cur_day = start_day
    while days_inserted < 3:
        if cur_day.weekday() < 5:
            prices = [100 + i * 0.1 for i in range(26)]
            bars = _make_rth_bars_for_day(cur_day, prices)
            with engine.begin() as conn:
                conn.execute(bars_table.insert(), bars)
            days_inserted += 1
        cur_day = cur_day + timedelta(days=1)
    engine.dispose()
    # Set DATABASE_URL so the CLI uses our temporary database
    monkeypatch.setenv("DATABASE_URL", db_url)
    # Run backtest via CLI
    runner = CliRunner()
    out_dir = tmp_path / "bt_warmup_out"
    result = runner.invoke(
        cli,
        [
            "backtest",
            "--start",
            start_day.isoformat(),
            "--end",
            (start_day + timedelta(days=2)).isoformat(),
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
    # trades.csv should have only header (no trades)
    with trades_csv.open() as f:
        lines = [line.strip() for line in f.readlines() if line.strip()]
    assert len(lines) == 1, f"Expected no trades during warmup, got lines: {lines}"
    # reasons.csv should include WARMUP_INSUFFICIENT_BARS
    with reasons_csv.open() as f:
        reasons_lines = [line.strip() for line in f.readlines() if line.strip()]
    # Header + at least one reason row
    assert len(reasons_lines) >= 2, "reasons.csv should contain header and at least one reason"
    # Check that any reason is warmup
    has_warmup = any("WARMUP_INSUFFICIENT_BARS" in row for row in reasons_lines[1:])
    assert has_warmup, f"Expected WARMUP_INSUFFICIENT_BARS reason, got: {reasons_lines}"


def test_backtest_warmup_allows_trading_after_threshold(tmp_path, monkeypatch):
    """Once the warmup threshold is satisfied, backtest should produce trades."""
    # Patch indicator and regime functions to deterministic stubs.  Patch on
    # informer.backtest.strategy to ensure the baseline strategy uses the
    # stubs.  See test_backtest_warmup_blocks_until_threshold for details.
    monkeypatch.setattr(
        "informer.backtest.strategy.compute_indicators", _stub_indicators
    )
    monkeypatch.setattr(
        "informer.backtest.strategy.compute_regimes", _stub_regimes
    )
    # Setup temporary SQLite database
    db_path = tmp_path / "warmup2.db"
    db_url = f"sqlite:///{db_path}"
    engine = create_engine(db_url)
    metadata.create_all(engine)
    # Insert bars for 10 trading days (10*26 = 260 bars >= 200)
    start_day = date(2025, 1, 6)  # Monday
    days_inserted = 0
    cur_day = start_day
    while days_inserted < 10:
        if cur_day.weekday() < 5:
            prices = [100 + i * 0.1 for i in range(26)]
            bars = _make_rth_bars_for_day(cur_day, prices)
            with engine.begin() as conn:
                conn.execute(bars_table.insert(), bars)
            days_inserted += 1
        cur_day = cur_day + timedelta(days=1)
    engine.dispose()
    # Set DATABASE_URL so the CLI uses our temporary database
    monkeypatch.setenv("DATABASE_URL", db_url)
    # Run backtest via CLI
    runner = CliRunner()
    out_dir = tmp_path / "bt_warmup2_out"
    # Determine the end date for the backtest based on the last inserted trading day.
    # At this point, cur_day is one day after the last trading day inserted in
    # the loop above (regardless of weekends).  Subtract one day to obtain
    # the date of the last bar.  This ensures that the CLI invocation
    # encompasses all inserted trading days and satisfies the warmup
    # requirement by 10:15 local time.
    end_day = cur_day - timedelta(days=1)
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
            "10:15",
            "--decision-tz",
            "America/New_York",
            "--out-dir",
            str(out_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    # Load trades.csv
    trades_csv = out_dir / "trades.csv"
    reasons_csv = out_dir / "reasons.csv"
    assert trades_csv.exists(), "trades.csv missing"
    assert reasons_csv.exists(), "reasons.csv missing"
    # trades.csv should have at least one trade row (header + >=1)
    with trades_csv.open() as f:
        lines = [line.strip() for line in f.readlines() if line.strip()]
    assert len(lines) > 1, f"Expected trades after warmup threshold, got lines: {lines}"
    # reasons.csv may contain WARMUP reasons at start; ensure at least one warmup reason exists
    with reasons_csv.open() as f:
        reasons_lines = [line.strip() for line in f.readlines() if line.strip()]
    # Ensure there is at least one warmup reason (since early days are below threshold)
    warmup_present = any("WARMUP_INSUFFICIENT_BARS" in row for row in reasons_lines[1:])
    assert warmup_present, "Expected at least one WARMUP_INSUFFICIENT_BARS reason"