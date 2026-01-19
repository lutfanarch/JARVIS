"""Tests for deterministic parameter selection in walk‑forward validation.

This test ensures that the tie‑break logic used during the parameter
selection phase of walk‑forward validation is deterministic.  When
multiple parameter combinations yield identical objective scores, the
implementation should select the lexicographically smallest set of
parameters based on k_stop, k_target and score_threshold.  The test
creates synthetic bar data for a single symbol, monkeypatches
indicator and regime computations to deterministic stubs, and runs
the walk‑forward CLI.  It then asserts that the first fold uses
the smallest parameter combination and that all folds in the walk‑
forward output remain consistent under the tie scenario.
"""

from datetime import datetime, date, time, timedelta
from typing import List, Dict, Any, Iterable

import csv
import json

import pytest
from click.testing import CliRunner
from sqlalchemy import create_engine

from informer.cli import cli
from informer.ingestion.bars import metadata, bars_table  # to create schema


def _make_constant_bars_for_day(trading_day: date, prices: List[float]) -> List[Dict[str, Any]]:
    """Build synthetic 15‑minute bars with constant prices for a single trading day.

    All OHLC values are set to the same price so that stops/targets are
    never hit and trades exit at end of day.  The bars cover the
    Regular Trading Hours starting at 09:30 Eastern time.  Timestamps
    are stored in UTC.
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
    """Indicator stub returning constant indicator values for each bar.

    The values are chosen so that the baseline strategy generates a
    candidate for each day when warmup is satisfied.  ATR is fixed at
    1.0 to simplify stop/target distances and ensure identical
    objective values across parameter combinations when costs are zero.
    """
    out: List[Dict[str, Any]] = []
    for b in bars:
        ts = b.get("ts") if isinstance(b, dict) else getattr(b, "ts", None)
        close = b.get("close") if isinstance(b, dict) else getattr(b, "close", None)
        out.append(
            {
                "ts": ts,
                "ema20": 2.5,
                "ema50": 1.0,
                "ema200": 1.0,
                "rsi14": 50.0,
                "atr14": 1.0,
                "vwap": close if close is not None else 100.0,
            }
        )
    return out


def _stub_regimes(
    bars: Iterable[Dict[str, Any]], indicators: List[Dict[str, Any]], timeframe: str
) -> List[Dict[str, Any]]:
    """Regime stub returning bullish regimes for all bars.

    Returns ``uptrend`` for the trend regime and ``low`` for the
    volatility regime so that the baseline strategy accepts trade
    candidates when warmup and score thresholds are satisfied.
    """
    out: List[Dict[str, Any]] = []
    n = min(len(bars), len(indicators))
    for i in range(n):
        ts = indicators[i].get("ts")
        out.append({"ts": ts, "trend_regime": "uptrend", "vol_regime": "low"})
    return out


def test_walkforward_tiebreak_deterministic(tmp_path, monkeypatch):
    """Walk‑forward should select the smallest parameter combination under ties.

    This test builds a synthetic dataset for AAPL with constant prices and
    runs the walk‑forward CLI with a parameter grid that produces
    identical objective scores across all combinations.  It asserts
    that the parameters for the first fold (and all folds) are the
    lexicographically smallest set according to the tie‑break rules.
    """
    # Change current working directory to tmp_path so that relative paths are under tmp_path
    monkeypatch.chdir(tmp_path)
    # Monkeypatch indicator and regime computations to deterministic stubs
    monkeypatch.setattr(
        "informer.backtest.strategy.compute_indicators", _stub_indicators
    )
    monkeypatch.setattr(
        "informer.backtest.strategy.compute_regimes", _stub_regimes
    )
    # Set up temporary SQLite database
    db_path = tmp_path / "wf_tiebreak.db"
    db_url = f"sqlite:///{db_path}"
    engine = create_engine(db_url)
    metadata.create_all(engine)
    # Insert 12 trading days of constant bars starting on a Monday
    start_day = date(2025, 1, 6)  # Monday
    days_inserted = 0
    cur_day = start_day
    first_inserted_day: date = start_day
    tenth_inserted_day: date = start_day
    prices = [100.0 for _ in range(26)]  # 26 bars per day
    while days_inserted < 12:
        if cur_day.weekday() < 5:
            bars = _make_constant_bars_for_day(cur_day, prices)
            with engine.begin() as conn:
                conn.execute(bars_table.insert(), bars)
            days_inserted += 1
            # Record first and tenth inserted trading days for CLI arguments
            if days_inserted == 1:
                first_inserted_day = cur_day
            if days_inserted == 10:
                tenth_inserted_day = cur_day
        cur_day = cur_day + timedelta(days=1)
    engine.dispose()
    # Use first and tenth inserted trading days as start and end of the walkforward
    start_date = first_inserted_day
    end_date = tenth_inserted_day
    # Set DATABASE_URL so the CLI uses our temporary database
    monkeypatch.setenv("DATABASE_URL", db_url)
    runner = CliRunner()
    out_dir = tmp_path / "wf_out"
    result = runner.invoke(
        cli,
        [
            "backtest-walkforward",
            "--start",
            start_date.isoformat(),
            "--end",
            end_date.isoformat(),
            "--train-days",
            "3",
            "--test-days",
            "2",
            "--symbols",
            "AAPL",
            "--decision-time",
            "13:30",
            "--decision-tz",
            "America/New_York",
            "--out-dir",
            str(out_dir),
            "--k-stop-grid",
            "1.0,2.0",
            "--k-target-grid",
            "2.0",
            "--score-threshold-grid",
            "0.0",
            "--objective",
            "avg_r",
            # Zero costs to force identical objective values across parameter combinations
            "--slippage-bps",
            "0",
            "--commission-per-share",
            "0",
        ],
    )
    assert result.exit_code == 0, result.output
    # Verify that walkforward_folds.csv exists
    folds_csv = out_dir / "walkforward_folds.csv"
    assert folds_csv.exists(), "walkforward_folds.csv missing"
    # Read folds CSV and parse parameter JSON in each row
    with folds_csv.open() as f:
        reader = csv.reader(f)
        rows = list(reader)
    # There should be at least one fold row
    assert len(rows) > 1, "Expected at least one fold row"
    header = rows[0]
    data_rows = rows[1:]
    # Identify the index of the params column
    try:
        params_idx = header.index("params")
    except ValueError:
        raise AssertionError("Column 'params' not found in folds CSV header")
    # Define the expected parameter set (smallest tuple under tie‑break)
    expected_params = {"k_stop": 1.0, "k_target": 2.0, "score_threshold": 0.0}
    # Check the first fold's parameters
    first_params_json = data_rows[0][params_idx]
    first_params = json.loads(first_params_json)
    assert first_params == expected_params, (
        f"First fold parameters {first_params} do not match expected {expected_params}"
    )
    # Optionally verify all folds use the same params under tie scenario
    for row in data_rows:
        params_json = row[params_idx]
        params = json.loads(params_json)
        assert params == expected_params, (
            f"Fold parameters {params} differ from expected {expected_params}"
        )