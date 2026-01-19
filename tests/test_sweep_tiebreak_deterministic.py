"""Tests for deterministic parameter sweep tie‑breaks.

This test suite verifies that the parameter sweep logic selects the
lexicographically smallest parameter combination when multiple
combinations yield identical objective scores.  It also ensures that
the sweep_results.csv is written in a stable order sorted by
parameter values (k_stop, k_target, score_threshold, extras).

The setup inserts synthetic bar data and monkeypatches indicator
and regime computations to deterministic stubs so that all
parameter combinations produce identical performance metrics.
"""

from datetime import datetime, date, time, timedelta
from typing import List, Dict, Any, Iterable

import csv
import json
import os

import pytest
from click.testing import CliRunner
from sqlalchemy import create_engine

from informer.cli import cli
from informer.ingestion.bars import metadata, bars_table  # to create schema


def _make_constant_bars_for_day(trading_day: date, prices: List[float]) -> List[Dict[str, Any]]:
    """Build synthetic 15m bars with constant prices for a single trading day.

    All OHLC values are set to the same price to avoid triggering stop
    or target exits.  Timestamps cover the Regular Trading Hours in
    15‑minute increments.  The returned dicts include keys required by
    the ingestion schema.
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
                "high": price,
                "low": price,
                "close": price,
                "volume": 1000,
                "vwap": price,
                "source": "TEST",
            }
        )
    return bars


def _stub_indicators(bars: Iterable[Dict[str, Any]], timeframe: str) -> List[Dict[str, Any]]:
    """Indicator stub that returns constant indicator values.

    Ignores input bar values and yields fixed indicator values aligned
    to the input length.  The values are chosen so that the baseline
    strategy will generate a candidate with a sufficiently high score
    across all parameter combinations.
    """
    out: List[Dict[str, Any]] = []
    for b in bars:
        ts = b.get("ts") if isinstance(b, dict) else getattr(b, "ts", None)
        close = b.get("close") if isinstance(b, dict) else getattr(b, "close", None)
        out.append(
            {
                "ts": ts,
                # Set ema values such that (ema20 - ema50)/atr14 yields a high score
                "ema20": 2.5,
                "ema50": 1.0,
                "ema200": 1.0,
                "rsi14": 50.0,
                # ATR fixed at 1.0 so that stop/target distances scale uniformly
                "atr14": 1.0,
                "vwap": close if close is not None else 100.0,
            }
        )
    return out


def _stub_regimes(
    bars: Iterable[Dict[str, Any]], indicators: List[Dict[str, Any]], timeframe: str
) -> List[Dict[str, Any]]:
    """Regime stub returning bullish regimes for all bars.

    Produces deterministic trend and volatility regimes so that the
    baseline strategy accepts candidates when warmup and score
    thresholds are met.
    """
    out: List[Dict[str, Any]] = []
    n = min(len(bars), len(indicators))
    for i in range(n):
        ts = indicators[i].get("ts")
        out.append({"ts": ts, "trend_regime": "uptrend", "vol_regime": "low"})
    return out


def test_sweep_tiebreak_deterministic(tmp_path, monkeypatch):
    """Parameter sweep selects the smallest param combination under ties.

    This test inserts sufficient bar history and runs the backtest
    sweep over a single day with multiple parameter combinations that
    yield identical objective scores.  The chosen best_params.json
    should correspond to the lexicographically smallest parameter tuple
    according to the deterministic tie‑break rules.  The sweep_results.csv
    should also list rows sorted by the same ordering.
    """
    # Monkeypatch indicator and regime computations to deterministic stubs
    monkeypatch.setattr(
        "informer.backtest.strategy.compute_indicators", _stub_indicators
    )
    monkeypatch.setattr(
        "informer.backtest.strategy.compute_regimes", _stub_regimes
    )
    # Setup temporary SQLite database
    db_path = tmp_path / "sweep_tiebreak.db"
    db_url = f"sqlite:///{db_path}"
    engine = create_engine(db_url)
    metadata.create_all(engine)
    # Insert 10 trading days of constant bars starting on a Monday
    start_day = date(2025, 1, 6)  # Monday
    days_inserted = 0
    cur_day = start_day
    last_day_inserted = start_day
    # Use 26 entries (15‑minute bars) with constant price 100.0
    prices = [100.0 for _ in range(26)]
    while days_inserted < 10:
        if cur_day.weekday() < 5:  # weekday only
            bars = _make_constant_bars_for_day(cur_day, prices)
            with engine.begin() as conn:
                conn.execute(bars_table.insert(), bars)
            days_inserted += 1
            last_day_inserted = cur_day
        cur_day = cur_day + timedelta(days=1)
    engine.dispose()
    # Use the last inserted trading day for the sweep
    sweep_date = last_day_inserted
    # Set DATABASE_URL so the CLI uses our temporary database
    monkeypatch.setenv("DATABASE_URL", db_url)
    # Run backtest sweep via CLI for a single day with a grid that will tie
    runner = CliRunner()
    out_dir = tmp_path / "sweep_out"
    result = runner.invoke(
        cli,
        [
            "backtest-sweep",
            "--start",
            sweep_date.isoformat(),
            "--end",
            sweep_date.isoformat(),
            "--symbols",
            "AAPL",
            "--decision-time",
            "10:15",
            "--decision-tz",
            "America/New_York",
            "--out-dir",
            str(out_dir),
            "--k-stop-grid",
            "1.0,2.0",
            "--k-target-grid",
            "2.0,3.0",
            "--score-threshold-grid",
            "0.0",
            "--objective",
            "total_pnl",
            # Set costs to zero so that all parameter combinations have identical objective scores
            "--slippage-bps",
            "0",
            "--commission-per-share",
            "0",
        ],
    )
    assert result.exit_code == 0, result.output
    # Verify best_params.json selects the smallest param tuple (1.0, 2.0, 0.0)
    best_path = out_dir / "best_params.json"
    assert best_path.exists(), "best_params.json missing"
    with best_path.open() as f:
        best_info = json.load(f)
    # Extract best_params
    best_params = best_info.get("best_params", {})
    assert best_params.get("k_stop") == 1.0, f"Expected k_stop 1.0, got {best_params.get('k_stop')}"
    assert best_params.get("k_target") == 2.0, f"Expected k_target 2.0, got {best_params.get('k_target')}"
    assert best_params.get("score_threshold") == 0.0 or best_params.get("score_threshold") is None
    # Verify sweep_results.csv ordering
    sweep_csv = out_dir / "sweep_results.csv"
    assert sweep_csv.exists(), "sweep_results.csv missing"
    with sweep_csv.open() as f:
        reader = csv.reader(f)
        rows = list(reader)
    # First row is header; subsequent rows correspond to parameter combos
    assert len(rows) > 1, "Sweep results should contain header and at least one data row"
    header = rows[0]
    data_rows = rows[1:]
    # Determine indices of parameter columns
    ks_idx = header.index("k_stop")
    kt_idx = header.index("k_target")
    st_idx = header.index("score_threshold")
    # Extract first data row and verify it matches the smallest param tuple
    first = data_rows[0]
    first_ks = float(first[ks_idx]) if first[ks_idx] != "" else None
    first_kt = float(first[kt_idx]) if first[kt_idx] != "" else None
    st_val = first[st_idx]
    first_st = float(st_val) if st_val not in ("", None) else 0.0
    assert first_ks == 1.0 and first_kt == 2.0 and first_st == 0.0