"""Phase 3 validation CLI tests for sweep and walk‑forward commands.

This module contains end‑to‑end tests exercising the new `backtest-sweep`
and `backtest-walkforward` CLI commands introduced in Phase 3.  The
tests create synthetic bar data in an in‑memory SQLite database,
monkeypatch the expensive indicator and regime computations to
deterministic stubs, run the CLI commands via Click and verify that
artifacts are produced with the expected structure.  These tests do
not attempt to validate numerical correctness of metrics; instead
they ensure that the validation harness executes end‑to‑end and
writes the required files.  The stubbed indicators produce a
constant uptrend/low volatility regime so trades are generated on
every day in the synthetic data.
"""

from datetime import datetime, date, time, timedelta
import json
import os
from typing import List, Dict, Any, Iterable

import pytest
from click.testing import CliRunner

from informer.cli import cli
from informer.ingestion.bars import metadata, bars_table  # to create schema
from informer.ingestion.bars import bars_table as _bars_table
from sqlalchemy import create_engine


def _make_bars_for_day(trading_day: date, prices: List[float]) -> List[dict]:
    """Helper to build 15m bar dicts with UTC timestamps.

    Each bar covers a 15‑minute interval starting at 09:30 ET.  Prices are
    provided as a list of floats representing the bar close; open/high/low
    values are derived deterministically from the close for simplicity.
    """
    from zoneinfo import ZoneInfo
    bars = []
    # Start at 09:30 Eastern time for typical US markets
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


def _setup_database(tmp_path) -> str:
    """Create an in‑memory SQLite database file and return its URL.

    The bars table is created using the metadata from the ingestion module.
    """
    db_path = tmp_path / "phase3.db"
    db_url = f"sqlite:///{db_path}"
    engine = create_engine(db_url)
    metadata.create_all(engine)
    engine.dispose()
    return db_url


def _stub_indicators(bars: Iterable[Dict[str, Any]], timeframe: str) -> List[Dict[str, Any]]:
    """Return deterministic indicator values for each bar.

    This stub ignores the actual bar data and returns a list of dicts
    matching the length of the input.  Each dict includes fields used
    by the baseline strategy: ema20, ema50, ema200, rsi14, atr14 and vwap.
    The values are chosen so that the baseline strategy produces
    a positive score and thus generates trades when score_threshold <= 1.0.
    """
    out = []
    # Collect timestamps and close prices from input bars; fall back to None
    for b in bars:
        # Extract timestamp if present
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
    indicators.  The return value length matches the shorter of
    ``bars`` and ``indicators``.
    """
    out = []
    it_bars = list(bars)
    n = min(len(it_bars), len(indicators))
    for i in range(n):
        ts = indicators[i].get("ts")
        out.append({"ts": ts, "trend_regime": "uptrend", "vol_regime": "low"})
    return out


def test_cli_backtest_sweep_runs_and_writes_artifacts(tmp_path, monkeypatch):
    """Run the backtest-sweep CLI against synthetic data and verify artifacts.

    This test populates a small SQLite database with three trading days of
    synthetic 15‑minute bars for AAPL.  It monkeypatches the indicator
    and regime computations to deterministic stubs so that a trade is
    generated on each day.  The sweep command is invoked with a simple
    grid and the resulting artifacts are verified for existence and basic
    structure.
    """
    # Patch indicator and regime functions to deterministic stubs
    monkeypatch.setattr(
        "informer.features.indicators.compute_indicators", _stub_indicators
    )
    monkeypatch.setattr(
        "informer.features.regimes.compute_regimes", _stub_regimes
    )
    # Setup temporary database
    db_url = _setup_database(tmp_path)
    monkeypatch.setenv("DATABASE_URL", db_url)
    # Insert synthetic bars for three consecutive days
    engine = create_engine(db_url)
    # Three weekdays starting from 2025‑01‑02
    start_day = date(2025, 1, 2)
    for day_offset in range(3):
        d = start_day + timedelta(days=day_offset)
        # Skip weekends (if any) by checking weekday (0=Mon, 6=Sun)
        if d.weekday() >= 5:
            continue
        prices = [100 + i * 0.1 for i in range(26)]
        bars = _make_bars_for_day(d, prices)
        with engine.begin() as conn:
            conn.execute(_bars_table.insert(), bars)
    engine.dispose()
    # Prepare output directory
    out_dir = tmp_path / "sweep_out"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "backtest-sweep",
            "--start",
            "2025-01-02",
            "--end",
            "2025-01-04",
            "--symbols",
            "AAPL",
            "--decision-time",
            "13:30",
            "--decision-tz",
            "America/New_York",
            "--out-dir",
            str(out_dir),
            "--k-stop-grid",
            "1.0",
            "--k-target-grid",
            "2.0",
            "--score-threshold-grid",
            "0.0",
            "--objective",
            "avg_r",
        ],
    )
    assert result.exit_code == 0, result.output
    # The sweep command should write directly into out_dir without run_id
    assert out_dir.exists() and out_dir.is_dir()
    sweep_results = out_dir / "sweep_results.csv"
    best_params = out_dir / "best_params.json"
    best_run_dir = out_dir / "best_run"
    assert sweep_results.exists(), "sweep_results.csv not found"
    assert best_params.exists(), "best_params.json not found"
    assert best_run_dir.is_dir(), "best_run directory missing"
    # Check that best_run contains trades.csv and summary.json
    trades_csv = best_run_dir / "trades.csv"
    summary_json = best_run_dir / "summary.json"
    assert trades_csv.exists(), "best_run/trades.csv missing"
    assert summary_json.exists(), "best_run/summary.json missing"
    # Ensure trades CSV has header plus at least one trade row
    with trades_csv.open() as f:
        lines = [line.strip() for line in f.readlines()]
    assert len(lines) >= 2, "No trades found in best_run/trades.csv"
    # Parse summary JSON and verify basic metrics keys
    with summary_json.open() as f:
        summary_data = json.load(f)
    assert "metrics" in summary_data
    metrics = summary_data["metrics"]
    for key in [
        "trades",
        "win_rate",
        "total_pnl",
        "max_drawdown",
        "max_drawdown_pct",
        "expectancy_r",
        "profit_factor",
    ]:
        assert key in metrics, f"Metric '{key}' missing from summary"


def test_cli_backtest_walkforward_runs_and_writes_artifacts(tmp_path, monkeypatch):
    """Run the backtest-walkforward CLI against synthetic data and verify artifacts.

    This test creates a longer synthetic dataset over ten trading days and
    runs the walk‑forward validation with a holdout period.  It verifies
    that fold summaries, OOS and holdout summaries are written and that
    regime breakdowns are present in the summary JSON files.
    """
    # Patch indicator and regime functions to deterministic stubs
    monkeypatch.setattr(
        "informer.features.indicators.compute_indicators", _stub_indicators
    )
    monkeypatch.setattr(
        "informer.features.regimes.compute_regimes", _stub_regimes
    )
    # Setup temporary database
    db_url = _setup_database(tmp_path)
    monkeypatch.setenv("DATABASE_URL", db_url)
    engine = create_engine(db_url)
    # Generate synthetic bars for at least 10 trading days (skip weekends)
    start_day = date(2025, 1, 2)
    days_added = 0
    cur_day = start_day
    while days_added < 12:  # generate more than needed to accommodate weekends
        if cur_day.weekday() < 5:
            prices = [100 + i * 0.1 for i in range(26)]
            bars = _make_bars_for_day(cur_day, prices)
            with engine.begin() as conn:
                conn.execute(_bars_table.insert(), bars)
            days_added += 1
        cur_day = cur_day + timedelta(days=1)
    engine.dispose()
    # Determine start and end dates for CLI invocation based on inserted days
    all_days = [start_day + timedelta(days=i) for i in range(20) if (start_day + timedelta(days=i)).weekday() < 5]
    # Use the first 10 weekdays as start and end
    start_date = all_days[0]
    end_date = all_days[9]
    # Holdout starts on the last two days of inserted range
    holdout_start_date = all_days[8]
    # Prepare output directory
    out_dir = tmp_path / "wf_out"
    runner = CliRunner()
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
            "1.0",
            "--k-target-grid",
            "2.0",
            "--score-threshold-grid",
            "0.0",
            "--objective",
            "avg_r",
            "--holdout-start",
            holdout_start_date.isoformat(),
        ],
    )
    assert result.exit_code == 0, result.output
    # Walk‑forward command should write directly into out_dir
    assert out_dir.exists() and out_dir.is_dir()
    # Check existence of core artifact files
    folds_csv = out_dir / "walkforward_folds.csv"
    oos_trades_csv = out_dir / "oos_trades.csv"
    oos_summary_json = out_dir / "oos_summary.json"
    holdout_trades_csv = out_dir / "holdout_trades.csv"
    holdout_summary_json = out_dir / "holdout_summary.json"
    assert folds_csv.exists(), "walkforward_folds.csv missing"
    assert oos_trades_csv.exists(), "oos_trades.csv missing"
    assert oos_summary_json.exists(), "oos_summary.json missing"
    assert holdout_trades_csv.exists(), "holdout_trades.csv missing"
    assert holdout_summary_json.exists(), "holdout_summary.json missing"
    # Load and inspect OOS summary JSON
    with oos_summary_json.open() as f:
        oos_summary = json.load(f)
    for key in [
        "trades",
        "win_rate",
        "total_pnl",
        "max_drawdown",
        "max_drawdown_pct",
        "regime_breakdown",
    ]:
        assert key in oos_summary, f"Key '{key}' missing from oos_summary"
    # Ensure regime_breakdown contains expected sections
    rb = oos_summary.get("regime_breakdown", {})
    assert "trend_regime_1h" in rb and "vol_regime_15m" in rb and "combined" in rb
    # Load and inspect holdout summary JSON
    with holdout_summary_json.open() as f:
        holdout_summary = json.load(f)
    for key in [
        "trades",
        "win_rate",
        "total_pnl",
        "max_drawdown",
        "max_drawdown_pct",
        "regime_breakdown",
    ]:
        assert key in holdout_summary, f"Key '{key}' missing from holdout_summary"
    rb_h = holdout_summary.get("regime_breakdown", {})
    assert "trend_regime_1h" in rb_h and "vol_regime_15m" in rb_h and "combined" in rb_h

    # Additional check: ensure OOS trades do not include any dates on or after the holdout start
    # Read the OOS trades CSV and parse the "date" column
    # All trade dates should be strictly before the holdout_start_date used in the CLI invocation
    with oos_trades_csv.open() as f:
        lines = [line.strip() for line in f.readlines() if line.strip()]
    # First line is header; subsequent lines are trades (if any)
    trade_dates = []
    for row in lines[1:]:
        cols = row.split(",")
        # Header ordering in write_trades_csv includes date as second column
        # Ensure we have at least two columns
        if len(cols) >= 2:
            trade_dates.append(cols[1])
    # Convert holdout_start_date string to ISO for comparison
    holdout_iso = holdout_start_date.isoformat()
    # Assert all trade dates precede the holdout start
    for tdate in trade_dates:
        assert tdate < holdout_iso, f"OOS trade date {tdate} is not strictly before holdout start {holdout_iso}"


def test_cli_backtest_sweep_includes_all_param_combinations(tmp_path, monkeypatch):
    """Ensure backtest-sweep writes a row per parameter combination when grid > 10.

    This test constructs a grid with 12 parameter combinations and verifies
    that sweep_results.csv contains one data row per combination in addition
    to the header.  The indicator and regime computations are stubbed so
    that trades are generated on each day.
    """
    # Stub indicator and regime functions
    monkeypatch.setattr(
        "informer.features.indicators.compute_indicators", _stub_indicators
    )
    monkeypatch.setattr(
        "informer.features.regimes.compute_regimes", _stub_regimes
    )
    # Setup temporary database
    db_url = _setup_database(tmp_path)
    monkeypatch.setenv("DATABASE_URL", db_url)
    engine = create_engine(db_url)
    # Insert synthetic bars for three consecutive trading days
    start_day = date(2025, 1, 6)  # Monday
    days_inserted = 0
    cur_day = start_day
    while days_inserted < 3:
        if cur_day.weekday() < 5:
            prices = [100 + i * 0.1 for i in range(26)]
            bars = _make_bars_for_day(cur_day, prices)
            with engine.begin() as conn:
                conn.execute(_bars_table.insert(), bars)
            days_inserted += 1
        cur_day = cur_day + timedelta(days=1)
    engine.dispose()
    # Prepare output directory
    out_dir = tmp_path / "sweep_full_out"
    runner = CliRunner()
    # Construct a grid with 12 combos: 3 values for k_stop, 4 for k_target, 1 for score_threshold
    result = runner.invoke(
        cli,
        [
            "backtest-sweep",
            "--start",
            start_day.isoformat(),
            "--end",
            (start_day + timedelta(days=2)).isoformat(),
            "--symbols",
            "AAPL",
            "--decision-time",
            "13:30",
            "--decision-tz",
            "America/New_York",
            "--out-dir",
            str(out_dir),
            "--k-stop-grid",
            "1.0,1.5,2.0",
            "--k-target-grid",
            "2.0,2.5,3.0,3.5",
            "--score-threshold-grid",
            "0.0",
            "--objective",
            "avg_r",
        ],
    )
    assert result.exit_code == 0, result.output
    sweep_results = out_dir / "sweep_results.csv"
    assert sweep_results.exists(), "sweep_results.csv not found"
    # Read lines and verify row count: header + 12 combinations
    with sweep_results.open() as f:
        rows = [line.strip() for line in f.readlines() if line.strip()]
    # First line is header, rest should equal number of param combos
    assert len(rows) - 1 == 12, f"Expected 12 parameter rows, got {len(rows) - 1}"


def test_cli_backtest_sweep_warmup_includes_previous_trading_day(tmp_path, monkeypatch):
    """Ensure that warmup logic includes the previous trading day's bars.

    This test inserts bars for two consecutive trading days and runs backtest-sweep
    starting on the second trading day.  We monkeypatch compute_indicators
    to assert that the bars passed for 15m computation include at least one bar
    from the prior trading day (warmup).  If warmup is not effective, the
    assertion will fail.
    """
    # Determine two consecutive trading days (skip weekends)
    day1 = date(2025, 1, 6)  # Monday
    day2 = day1 + timedelta(days=1)  # Tuesday
    # Setup database and insert bars for both days
    db_url = _setup_database(tmp_path)
    monkeypatch.setenv("DATABASE_URL", db_url)
    engine = create_engine(db_url)
    for d in [day1, day2]:
        prices = [100 + i * 0.1 for i in range(26)]
        bars = _make_bars_for_day(d, prices)
        with engine.begin() as conn:
            conn.execute(_bars_table.insert(), bars)
    engine.dispose()
    # Compute expected warmup date (previous trading day)
    expected_warmup_date = day1
    # Define a stub for compute_indicators that asserts warmup bar presence
    from zoneinfo import ZoneInfo

    def _assert_warmup_indicators(bars: Iterable[Dict[str, Any]], timeframe: str):
        # Convert iterable to list so we can iterate multiple times
        b_list = list(bars)
        # Only assert on 15m timeframe (not on aggregated 1h)
        if timeframe == "15m":
            # Check at least one bar has local date equal to expected_warmup_date
            found = False
            for b in b_list:
                ts = b.get("ts") if isinstance(b, dict) else getattr(b, "ts", None)
                if ts is None:
                    continue
                local_date = ts.astimezone(ZoneInfo("America/New_York")).date()
                if local_date == expected_warmup_date:
                    found = True
                    break
            assert found, f"Warmup bar for {expected_warmup_date.isoformat()} not found in bars"
        # Return deterministic indicator structure similar to _stub_indicators
        out = []
        for b in b_list:
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

    # Patch indicator and regime functions
    monkeypatch.setattr(
        "informer.features.indicators.compute_indicators", _assert_warmup_indicators
    )
    monkeypatch.setattr(
        "informer.features.regimes.compute_regimes", _stub_regimes
    )
    # Prepare CLI invocation starting on the second trading day (day2)
    out_dir = tmp_path / "warmup_out"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "backtest-sweep",
            "--start",
            day2.isoformat(),
            "--end",
            day2.isoformat(),
            "--symbols",
            "AAPL",
            "--decision-time",
            "13:30",
            "--decision-tz",
            "America/New_York",
            "--out-dir",
            str(out_dir),
            "--k-stop-grid",
            "1.0",
            "--k-target-grid",
            "2.0",
            "--score-threshold-grid",
            "0.0",
            "--objective",
            "avg_r",
        ],
    )
    # The stub assertion is triggered inside compute_indicators; if warmup was not included,
    # an AssertionError would be raised and exit_code would be non-zero.  Otherwise exit_code is 0.
    assert result.exit_code == 0, result.output


def test_write_trades_csv_empty_header_includes_phase3_fields(tmp_path):
    """Verify that write_trades_csv writes Phase 3 fields when trades list is empty.

    The CSV header should include score, vol_regime_15m and trend_regime_1h
    when no trades are present, matching the fields available in a non-empty run.
    """
    from informer.backtest.io import write_trades_csv
    # Create an output file in the temporary directory
    out_path = tmp_path / "empty_trades.csv"
    # Write an empty trades list
    write_trades_csv([], str(out_path))
    # Read the header line
    with out_path.open() as f:
        header = f.readline().strip().split(",")
    # Assert that Phase 3 columns are present
    assert "score" in header, "score column missing from empty trades CSV header"
    assert "vol_regime_15m" in header, "vol_regime_15m column missing from empty trades CSV header"
    assert "trend_regime_1h" in header, "trend_regime_1h column missing from empty trades CSV header"


def test_cli_backtest_walkforward_writes_artifacts_when_no_trades(tmp_path, monkeypatch):
    """Ensure walk-forward CLI writes artifacts even when no trades are generated.

    This test uses a high score_threshold to prevent any trades from being executed.  It
    verifies that oos and holdout trade files are still written (with only headers), and
    that the summaries contain zero trades and include regime breakdown keys.
    """
    # Patch indicator and regime functions to deterministic stubs
    monkeypatch.setattr(
        "informer.features.indicators.compute_indicators", _stub_indicators
    )
    monkeypatch.setattr(
        "informer.features.regimes.compute_regimes", _stub_regimes
    )
    # Setup temporary database
    db_url = _setup_database(tmp_path)
    monkeypatch.setenv("DATABASE_URL", db_url)
    engine = create_engine(db_url)
    # Insert synthetic bars for at least 8 trading days to allow folds and holdout
    start_day = date(2025, 1, 6)  # Monday
    days_inserted = 0
    cur_day = start_day
    while days_inserted < 8:
        if cur_day.weekday() < 5:
            prices = [100 + i * 0.1 for i in range(26)]
            bars = _make_bars_for_day(cur_day, prices)
            with engine.begin() as conn:
                conn.execute(_bars_table.insert(), bars)
            days_inserted += 1
        cur_day = cur_day + timedelta(days=1)
    engine.dispose()
    # Determine trading days list for verifying holdout
    all_days = [start_day + timedelta(days=i) for i in range(20) if (start_day + timedelta(days=i)).weekday() < 5]
    # Use first 6 weekdays as evaluation window
    start_date = all_days[0]
    end_date = all_days[5]
    # Set holdout to last day in the window
    holdout_start_date = all_days[5]
    # Prepare output directory
    out_dir = tmp_path / "wf_no_trade_out"
    runner = CliRunner()
    # Invoke walk-forward with high score threshold (no trades)
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
            "1.0",
            "--k-target-grid",
            "2.0",
            "--score-threshold-grid",
            "999",  # very high threshold prevents any trades (stub score is ~1.0)
            "--objective",
            "avg_r",
            "--holdout-start",
            holdout_start_date.isoformat(),
        ],
    )
    assert result.exit_code == 0, result.output
    # Validate that core artifact files exist
    folds_csv = out_dir / "walkforward_folds.csv"
    oos_trades_csv = out_dir / "oos_trades.csv"
    oos_summary_json = out_dir / "oos_summary.json"
    holdout_trades_csv = out_dir / "holdout_trades.csv"
    holdout_summary_json = out_dir / "holdout_summary.json"
    assert folds_csv.exists(), "walkforward_folds.csv missing"
    assert oos_trades_csv.exists(), "oos_trades.csv missing"
    assert oos_summary_json.exists(), "oos_summary.json missing"
    assert holdout_trades_csv.exists(), "holdout_trades.csv missing"
    assert holdout_summary_json.exists(), "holdout_summary.json missing"
    # Read oos_trades.csv; it should have only header (no data rows)
    with oos_trades_csv.open() as f:
        lines = [line.strip() for line in f.readlines() if line.strip()]
    # At least header must be present
    assert len(lines) >= 1, "oos_trades.csv should have at least one header line"
    # There should be no trade rows (header only)
    assert len(lines) == 1, f"oos_trades.csv should contain only header when no trades, got {len(lines)} lines"
    # Similarly for holdout_trades.csv
    with holdout_trades_csv.open() as f:
        hl_lines = [line.strip() for line in f.readlines() if line.strip()]
    assert len(hl_lines) >= 1, "holdout_trades.csv should have at least header line"
    assert len(hl_lines) == 1, f"holdout_trades.csv should contain only header when no trades, got {len(hl_lines)} lines"
    # Load summaries and verify trades == 0 and regime_breakdown key exists
    with oos_summary_json.open() as f:
        oos_summary = json.load(f)
    assert oos_summary.get("trades") == 0, "oos_summary should report zero trades"
    assert "regime_breakdown" in oos_summary, "oos_summary missing regime_breakdown"
    with holdout_summary_json.open() as f:
        h_summary = json.load(f)
    assert h_summary.get("trades") == 0, "holdout_summary should report zero trades"
    assert "regime_breakdown" in h_summary, "holdout_summary missing regime_breakdown"