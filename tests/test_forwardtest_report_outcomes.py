"""Tests for forward-test report including realised outcomes.

This module exercises the ``forwardtest report`` command extended to
include realised outcome metrics.  It constructs a temporary
forward-test registry and outcomes log, invokes the report
CLI for different date ranges and verifies that outcome-aware metrics
are computed correctly, unmatched outcomes are skipped and profit
factor handling covers both loss and no-loss scenarios.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from informer.cli import cli


def _setup_forward_test_data(base_dir: Path) -> tuple[str, str]:
    """Create sample forward-test runs, decisions and outcomes.

    This helper writes two forward-test run entries with TRADE status and
    associated decision artifacts.  It also logs three outcome
    records—two matched to the runs and one unmatched.  It returns
    the run identifiers for the two matched runs.

    Parameters
    ----------
    base_dir: Path
        Temporary directory under which to create artifacts.

    Returns
    -------
    tuple[str, str]
        The run IDs of the two matched forward-test runs.
    """
    # Directories
    ft_dir = base_dir / "artifacts" / "forward_test"
    ft_dir.mkdir(parents=True, exist_ok=True)
    runs_file = ft_dir / "forward_test_runs.jsonl"
    outcomes_file = ft_dir / "forward_test_outcomes.jsonl"
    runs: list[dict] = []
    # Run 1: 2026-01-01, symbol AAPL
    run1_id = "run1"
    ny_date1 = "2026-01-01"
    created1 = "2026-01-01T12:00:00Z"
    symbol1 = "AAPL"
    artifact_dir1 = ft_dir / ny_date1 / run1_id
    artifact_dir1.mkdir(parents=True, exist_ok=True)
    # Decision file for run1
    decision1 = {
        "entry": 100.0,
        "stop": 95.0,
        "shares": 1,
        "symbol": symbol1,
        "action": "TRADE",
    }
    with (artifact_dir1 / "decision.json").open("w", encoding="utf-8") as f:
        json.dump(decision1, f)
    run1_record = {
        "run_id": run1_id,
        "ny_date": ny_date1,
        "created_at_utc": created1,
        "mode": "shadow",
        "symbols": [symbol1],
        "decision_status": "TRADE",
        "selected_symbol": symbol1,
        "rationale_summary": None,
        "schema_version": None,
        "config_hash": "abc123",
        "artifact_dir": str(artifact_dir1),
        "lock_key": "lock1",
    }
    runs.append(run1_record)
    # Run 2: 2026-01-02, symbol MSFT
    run2_id = "run2"
    ny_date2 = "2026-01-02"
    created2 = "2026-01-02T12:00:00Z"
    symbol2 = "MSFT"
    artifact_dir2 = ft_dir / ny_date2 / run2_id
    artifact_dir2.mkdir(parents=True, exist_ok=True)
    decision2 = {
        "entry": 200.0,
        "stop": 190.0,
        "shares": 1,
        "symbol": symbol2,
        "action": "TRADE",
    }
    with (artifact_dir2 / "decision.json").open("w", encoding="utf-8") as f:
        json.dump(decision2, f)
    run2_record = {
        "run_id": run2_id,
        "ny_date": ny_date2,
        "created_at_utc": created2,
        "mode": "shadow",
        "symbols": [symbol2],
        "decision_status": "TRADE",
        "selected_symbol": symbol2,
        "rationale_summary": None,
        "schema_version": None,
        "config_hash": "def456",
        "artifact_dir": str(artifact_dir2),
        "lock_key": "lock2",
    }
    runs.append(run2_record)
    # Write runs to JSONL
    with runs_file.open("w", encoding="utf-8") as f:
        for record in runs:
            json.dump(record, f, sort_keys=True)
            f.write("\n")
    # Outcomes: two matched, one unmatched
    outcomes = [
        {
            "ny_date": ny_date1,
            "symbol": symbol1,
            "entry": 100.0,
            "exit": 105.0,
            "notes": "win",
            "recorded_at_utc": "2026-01-04T00:00:00Z",
        },
        {
            "ny_date": ny_date2,
            "symbol": symbol2,
            "entry": 200.0,
            "exit": 195.0,
            "notes": "loss",
            "recorded_at_utc": "2026-01-04T01:00:00Z",
        },
        {
            # Unmatched outcome: no corresponding run
            "ny_date": "2026-01-03",
            "symbol": "XYZ",
            "entry": 100.0,
            "exit": 101.0,
            "notes": "unmatched",
            "recorded_at_utc": "2026-01-04T02:00:00Z",
        },
    ]
    with outcomes_file.open("w", encoding="utf-8") as f:
        for record in outcomes:
            json.dump(record, f, sort_keys=True)
            f.write("\n")
    return run1_id, run2_id


def test_forwardtest_report_outcomes_with_losses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Report should include outcome metrics and handle losses correctly."""
    # Set up test data
    run1_id, run2_id = _setup_forward_test_data(tmp_path)
    # Change working directory to the temporary path
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    # Invoke report over both dates
    out_path = "report.json"
    result = runner.invoke(
        cli,
        [
            "forwardtest",
            "report",
            "--start",
            "2026-01-01",
            "--end",
            "2026-01-02",
            "--out",
            out_path,
        ],
    )
    assert result.exit_code == 0, result.output
    report_file = tmp_path / out_path
    assert report_file.exists(), "Report file not created"
    with report_file.open("r", encoding="utf-8") as f:
        report = json.load(f)
    # Validate outcomes_summary
    osum = report.get("outcomes_summary", {})
    # Two matched outcomes
    assert osum.get("outcomes_total") == 2
    # One win (AAPL), one loss (MSFT) => win rate 0.5
    assert abs(osum.get("outcomes_win_rate") - 0.5) < 1e-6
    # Total pnl: 5 + (-5) = 0
    assert abs(osum.get("outcomes_total_pnl_usd") - 0.0) < 1e-6
    # Average pnl: 0 / 2 = 0
    assert abs(osum.get("outcomes_avg_pnl_usd") - 0.0) < 1e-6
    # Expectancy R: (1 + (-0.5)) / 2 = 0.25
    assert abs(osum.get("outcomes_expectancy_r") - 0.25) < 1e-6
    # Profit factor: sum wins (5) / abs(sum losses (-5)) = 1.0
    assert abs(osum.get("outcomes_profit_factor") - 1.0) < 1e-6
    # Max drawdown: first day +5, second day -5 -> equity sequence [5,0] => drawdown 5
    assert abs(osum.get("outcomes_max_drawdown_usd") - 5.0) < 1e-6
    # Validate outcomes_rows
    rows = report.get("outcomes_rows", [])
    # Should contain exactly two matched rows
    assert len(rows) == 2
    # Check that each row has expected run_id, realised entry/exit and metrics
    # Build helper dict keyed by (ny_date, symbol)
    row_map = {(r["ny_date"], r["symbol"]): r for r in rows}
    # Row for AAPL
    row1 = row_map.get(("2026-01-01", "AAPL"))
    assert row1 is not None
    assert row1["run_id"] == run1_id
    # Outcome provided entry 100, so entry_realised should come from outcome and entry_source should be 'outcome'
    assert abs(row1["entry_realised"] - 100.0) < 1e-6
    assert row1["entry_source"] == "outcome"
    assert abs(row1["exit_realised"] - 105.0) < 1e-6
    assert row1["shares"] == 1
    assert abs(row1["stop"] - 95.0) < 1e-6
    assert abs(row1["pnl_usd"] - 5.0) < 1e-6
    assert abs(row1["r"] - 1.0) < 1e-6
    # Row for MSFT
    row2 = row_map.get(("2026-01-02", "MSFT"))
    assert row2 is not None
    assert row2["run_id"] == run2_id
    # Outcome provided entry 200, so entry_realised should come from outcome and entry_source should be 'outcome'
    assert abs(row2["entry_realised"] - 200.0) < 1e-6
    assert row2["entry_source"] == "outcome"
    assert abs(row2["exit_realised"] - 195.0) < 1e-6
    assert row2["shares"] == 1
    assert abs(row2["stop"] - 190.0) < 1e-6
    # PnL for loss: (195 - 200) * 1 = -5
    assert abs(row2["pnl_usd"] + 5.0) < 1e-6  # loss
    # R for loss: (195 - 200) / (200 - 190) = -0.5
    assert abs(row2["r"] + 0.5) < 1e-6


def test_forwardtest_report_outcomes_no_losses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Report should set profit factor to null when there are no losses."""
    # Set up test data
    run1_id, run2_id = _setup_forward_test_data(tmp_path)
    # Change working directory
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    # Only include the first date to get a single positive outcome
    out_path = "report_positive.json"
    result = runner.invoke(
        cli,
        [
            "forwardtest",
            "report",
            "--start",
            "2026-01-01",
            "--end",
            "2026-01-01",
            "--out",
            out_path,
        ],
    )
    assert result.exit_code == 0, result.output
    report_file = tmp_path / out_path
    assert report_file.exists()
    with report_file.open("r", encoding="utf-8") as f:
        report = json.load(f)
    osum = report.get("outcomes_summary", {})
    # Only one matched outcome
    assert osum.get("outcomes_total") == 1
    assert abs(osum.get("outcomes_win_rate") - 1.0) < 1e-6
    # Profit factor should be null/None due to absence of losses
    assert osum.get("outcomes_profit_factor") is None
    # Validate outcomes_rows for the single outcome
    rows = report.get("outcomes_rows", [])
    assert len(rows) == 1
    row = rows[0]
    # Outcome provided entry, so entry_source should be 'outcome'
    assert row["entry_source"] == "outcome"


def test_forwardtest_report_outcomes_entry_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When outcome entry differs from decision entry, the report must use the outcome entry."""
    # Setup a single run and outcome where outcome entry overrides decision entry
    base_dir = tmp_path
    ft_dir = base_dir / "artifacts" / "forward_test"
    ft_dir.mkdir(parents=True, exist_ok=True)
    runs_file = ft_dir / "forward_test_runs.jsonl"
    outcomes_file = ft_dir / "forward_test_outcomes.jsonl"
    # Write run record
    run_id = "override_run"
    ny_date = "2026-02-01"
    symbol = "ABC"
    artifact_dir = ft_dir / ny_date / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    # Decision with entry 100, stop 95, shares 1
    decision = {
        "entry": 100.0,
        "stop": 95.0,
        "shares": 1,
        "symbol": symbol,
        "action": "TRADE",
    }
    with (artifact_dir / "decision.json").open("w", encoding="utf-8") as f:
        json.dump(decision, f)
    run_record = {
        "run_id": run_id,
        "ny_date": ny_date,
        "created_at_utc": "2026-02-01T12:00:00Z",
        "mode": "shadow",
        "symbols": [symbol],
        "decision_status": "TRADE",
        "selected_symbol": symbol,
        "rationale_summary": None,
        "schema_version": None,
        "config_hash": "hashoverride",
        "artifact_dir": str(artifact_dir),
        "lock_key": "lockoverride",
    }
    with runs_file.open("w", encoding="utf-8") as f:
        json.dump(run_record, f)
        f.write("\n")
    # Outcome: entry 99.0 (different from decision), exit 104.0
    outcome = {
        "ny_date": ny_date,
        "symbol": symbol,
        "entry": 99.0,
        "exit": 104.0,
        "notes": "override entry",
        "recorded_at_utc": "2026-02-02T00:00:00Z",
    }
    with outcomes_file.open("w", encoding="utf-8") as f:
        json.dump(outcome, f)
        f.write("\n")
    # Run report
    monkeypatch.chdir(base_dir)
    runner = CliRunner()
    out_path = "report_override.json"
    result = runner.invoke(
        cli,
        [
            "forwardtest",
            "report",
            "--start",
            ny_date,
            "--end",
            ny_date,
            "--out",
            out_path,
        ],
    )
    assert result.exit_code == 0, result.output
    report_file = base_dir / out_path
    assert report_file.exists()
    with report_file.open("r", encoding="utf-8") as f:
        report = json.load(f)
    osum = report.get("outcomes_summary", {})
    # One outcome, win (104-99=5)
    assert osum.get("outcomes_total") == 1
    assert abs(osum.get("outcomes_win_rate") - 1.0) < 1e-6
    assert abs(osum.get("outcomes_total_pnl_usd") - 5.0) < 1e-6
    assert abs(osum.get("outcomes_avg_pnl_usd") - 5.0) < 1e-6
    # Expectancy R: (5 / (99-95)) = 5/4 = 1.25
    assert abs(osum.get("outcomes_expectancy_r") - 1.25) < 1e-6
    # Profit factor should be None as no losses
    assert osum.get("outcomes_profit_factor") is None
    # Max drawdown is zero for single positive day
    assert abs(osum.get("outcomes_max_drawdown_usd")) < 1e-6
    # Validate row
    rows = report.get("outcomes_rows", [])
    assert len(rows) == 1
    row = rows[0]
    # entry_realised should come from outcome (99.0)
    assert abs(row["entry_realised"] - 99.0) < 1e-6
    assert row["entry_source"] == "outcome"
    assert abs(row["exit_realised"] - 104.0) < 1e-6
    assert row["shares"] == 1
    assert abs(row["stop"] - 95.0) < 1e-6
    assert abs(row["pnl_usd"] - 5.0) < 1e-6
    assert abs(row["r"] - 1.25) < 1e-6


def test_forwardtest_report_outcomes_entry_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When outcome entry is missing, the report must use the decision entry."""
    # Setup a single run and outcome where outcome entry is omitted
    base_dir = tmp_path
    ft_dir = base_dir / "artifacts" / "forward_test"
    ft_dir.mkdir(parents=True, exist_ok=True)
    runs_file = ft_dir / "forward_test_runs.jsonl"
    outcomes_file = ft_dir / "forward_test_outcomes.jsonl"
    # Write run record
    run_id = "missing_entry_run"
    ny_date = "2026-03-01"
    symbol = "DEF"
    artifact_dir = ft_dir / ny_date / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    decision = {
        "entry": 100.0,
        "stop": 95.0,
        "shares": 1,
        "symbol": symbol,
        "action": "TRADE",
    }
    with (artifact_dir / "decision.json").open("w", encoding="utf-8") as f:
        json.dump(decision, f)
    run_record = {
        "run_id": run_id,
        "ny_date": ny_date,
        "created_at_utc": "2026-03-01T12:00:00Z",
        "mode": "shadow",
        "symbols": [symbol],
        "decision_status": "TRADE",
        "selected_symbol": symbol,
        "rationale_summary": None,
        "schema_version": None,
        "config_hash": "hashmissing",
        "artifact_dir": str(artifact_dir),
        "lock_key": "lockmissing",
    }
    with runs_file.open("w", encoding="utf-8") as f:
        json.dump(run_record, f)
        f.write("\n")
    # Outcome: no entry key, exit 104.0 (so decision entry should be used)
    outcome = {
        "ny_date": ny_date,
        "symbol": symbol,
        "exit": 104.0,
        "notes": "missing entry",
        "recorded_at_utc": "2026-03-02T00:00:00Z",
    }
    with outcomes_file.open("w", encoding="utf-8") as f:
        json.dump(outcome, f)
        f.write("\n")
    # Run report
    monkeypatch.chdir(base_dir)
    runner = CliRunner()
    out_path = "report_missing.json"
    result = runner.invoke(
        cli,
        [
            "forwardtest",
            "report",
            "--start",
            ny_date,
            "--end",
            ny_date,
            "--out",
            out_path,
        ],
    )
    assert result.exit_code == 0, result.output
    report_file = base_dir / out_path
    assert report_file.exists()
    with report_file.open("r", encoding="utf-8") as f:
        report = json.load(f)
    osum = report.get("outcomes_summary", {})
    # One outcome, win (104-100=4)
    assert osum.get("outcomes_total") == 1
    assert abs(osum.get("outcomes_win_rate") - 1.0) < 1e-6
    assert abs(osum.get("outcomes_total_pnl_usd") - 4.0) < 1e-6
    assert abs(osum.get("outcomes_avg_pnl_usd") - 4.0) < 1e-6
    # Expectancy R: (4 / (100-95)) = 4/5 = 0.8
    assert abs(osum.get("outcomes_expectancy_r") - 0.8) < 1e-6
    # Profit factor None
    assert osum.get("outcomes_profit_factor") is None
    # Max drawdown zero
    assert abs(osum.get("outcomes_max_drawdown_usd")) < 1e-6
    # Validate row
    rows = report.get("outcomes_rows", [])
    assert len(rows) == 1
    row = rows[0]
    # entry_realised should come from decision (100.0)
    assert abs(row["entry_realised"] - 100.0) < 1e-6
    assert row["entry_source"] == "decision"
    assert abs(row["exit_realised"] - 104.0) < 1e-6
    assert row["shares"] == 1
    assert abs(row["stop"] - 95.0) < 1e-6
    assert abs(row["pnl_usd"] - 4.0) < 1e-6
    assert abs(row["r"] - 0.8) < 1e-6