"""Tests for the prop eval-status command.

These tests construct a minimal forward‑test registry and outcomes log
along with decision artifacts to exercise the evaluation status
report.  They verify that progress metrics, concentration and
drawdown warnings are computed deterministically for the Trade The Pool
25k beginner profile.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from informer.cli import cli


def _create_run(
    base_dir: Path,
    run_id: str,
    ny_date: str,
    created_at: str,
    symbol: str,
    entry: float,
    stop: float,
    shares: int,
    mode: str = "shadow",
) -> None:
    """Helper to write a run record and decision file.

    This writes an entry into forward_test_runs.jsonl and creates the
    decision.json file under artifacts/forward_test/<ny_date>/<run_id>/.
    It appends the run record to the JSONL file allowing multiple
    runs to be created easily.
    """
    ft_dir = base_dir / "artifacts" / "forward_test"
    runs_file = ft_dir / "forward_test_runs.jsonl"
    # Ensure directories exist
    ft_dir.mkdir(parents=True, exist_ok=True)
    # Build artifact directory for the run
    artifact_dir = ft_dir / ny_date / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    # Write decision.json
    decision = {
        "entry": entry,
        "stop": stop,
        "shares": shares,
        "symbol": symbol,
        "action": "TRADE",
    }
    with (artifact_dir / "decision.json").open("w", encoding="utf-8") as f:
        json.dump(decision, f)
    # Append run record
    record = {
        "run_id": run_id,
        "ny_date": ny_date,
        "created_at_utc": created_at,
        "mode": mode,
        "symbols": [symbol],
        "decision_status": "TRADE",
        "selected_symbol": symbol,
        "rationale_summary": None,
        "schema_version": None,
        "config_hash": f"hash-{run_id}",
        "artifact_dir": str(artifact_dir),
        "lock_key": f"lock-{run_id}",
    }
    # Append to JSONL
    with runs_file.open("a", encoding="utf-8") as f:
        json.dump(record, f, sort_keys=True)
        f.write("\n")


def _append_outcome(
    base_dir: Path,
    ny_date: str,
    symbol: str,
    entry: float,
    exit: float,
    notes: str = "",
    recorded_at: str = "2026-01-01T00:00:00Z",
    duration_seconds: int | None = 60,
) -> None:
    """Helper to append an outcome record to forward_test_outcomes.jsonl.

    A ``duration_seconds`` parameter may be supplied to record the
    realised trade duration.  When ``None``, the key is omitted from
    the JSON record, causing the outcome to have unknown validity.  A
    default duration of 60 s is used for tests where no explicit
    duration is provided to ensure that profitable outcomes count as
    valid per the evaluation profile’s minimum hold time requirement.
    """
    outcomes_file = base_dir / "artifacts" / "forward_test" / "forward_test_outcomes.jsonl"
    outcomes_file.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ny_date": ny_date,
        "symbol": symbol,
        "entry": entry,
        "exit": exit,
        "notes": notes,
        "recorded_at_utc": recorded_at,
    }
    if duration_seconds is not None:
        record["duration_seconds"] = int(duration_seconds)
    with outcomes_file.open("a", encoding="utf-8") as f:
        json.dump(record, f, sort_keys=True)
        f.write("\n")


def test_eval_status_best_trade_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A large best trade should trigger the concentration warning."""
    # Setup forward‑test runs and outcomes
    # Two profitable trades; first is larger and dominates profits
    _create_run(tmp_path, run_id="runA", ny_date="2026-01-01", created_at="2026-01-01T12:00:00Z", symbol="AAPL", entry=100.0, stop=95.0, shares=50)
    _append_outcome(tmp_path, ny_date="2026-01-01", symbol="AAPL", entry=100.0, exit=106.0, notes="win")  # +6*50=+300
    _create_run(tmp_path, run_id="runB", ny_date="2026-01-02", created_at="2026-01-02T12:00:00Z", symbol="MSFT", entry=200.0, stop=195.0, shares=50)
    _append_outcome(tmp_path, ny_date="2026-01-02", symbol="MSFT", entry=200.0, exit=202.0, notes="small win")  # +2*50=+100
    # Change working directory to the temp path
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    out_file = "report.json"
    # Invoke eval-status with explicit profile and start date
    result = runner.invoke(
        cli,
        ["prop", "eval-status", "--profile", "trade_the_pool_25k_beginner", "--start", "2026-01-01", "--out", out_file],
    )
    assert result.exit_code == 0, result.output
    report_path = tmp_path / out_file
    assert report_path.exists(), "Report JSON file not created"
    with report_path.open("r", encoding="utf-8") as f:
        report = json.load(f)
    # Total PnL should be +400
    assert abs(report["realised_total_pnl_usd"] - 400.0) < 1e-6
    # Progress percentage should be 400 / 1500 = 26.666...%
    assert abs(report["progress_to_target_pct"] - (400.0 / 1500.0 * 100.0)) < 1e-6
    # Best trade ratio: 300 / 400 = 0.75
    assert abs(report["best_trade_ratio"] - 0.75) < 1e-6
    # The concentration warning should be flagged
    warnings = report.get("warnings", {})
    assert warnings.get("best_trade_concentration") is True
    # No drawdown or daily loss warnings in this scenario
    assert warnings.get("max_drawdown_breached") is False
    assert warnings.get("daily_loss_violations") == []


def test_eval_status_drawdown_and_daily_loss_warnings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Large losses should trigger drawdown and daily loss warnings."""
    # One trade with significant loss
    _create_run(tmp_path, run_id="runL", ny_date="2026-01-03", created_at="2026-01-03T12:00:00Z", symbol="TSLA", entry=100.0, stop=90.0, shares=100)
    # Outcome: exit far below entry -> big loss
    _append_outcome(tmp_path, ny_date="2026-01-03", symbol="TSLA", entry=100.0, exit=80.0, notes="big loss")  # -20*100=-2000
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["prop", "eval-status", "--profile", "trade_the_pool_25k_beginner", "--start", "2026-01-03", "--out", "report_loss.json"],
    )
    assert result.exit_code == 0, result.output
    report_path = tmp_path / "report_loss.json"
    with report_path.open("r", encoding="utf-8") as f:
        report = json.load(f)
    warnings = report.get("warnings", {})
    # Total realised PnL should be -2000
    assert abs(report["realised_total_pnl_usd"] - (-2000.0)) < 1e-6
    # Best trade ratio should be None since total <= 0
    assert report["best_trade_ratio"] is None
    # Drawdown should be 2000 USD => drawdown% = 2000 / 25000 * 100 = 8%
    assert abs(report["max_drawdown_usd"] - 2000.0) < 1e-6
    assert abs(report["max_drawdown_pct"] - (2000.0 / 25000.0 * 100.0)) < 1e-6
    # Drawdown warning should be true (8% > 4% max_loss_pct)
    assert warnings.get("max_drawdown_breached") is True
    # Daily loss violations should include the date
    assert warnings.get("daily_loss_violations") == ["2026-01-03"]
    # No best trade concentration warning in this scenario
    assert warnings.get("best_trade_concentration") is False


def test_eval_status_no_matched_outcomes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When there are no matched trade outcomes, metrics default to zero and no warnings."""
    # Create a TRADE run but no matching outcome
    _create_run(tmp_path, run_id="runC", ny_date="2026-01-04", created_at="2026-01-04T12:00:00Z", symbol="NFLX", entry=300.0, stop=295.0, shares=10)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    res = runner.invoke(
        cli,
        ["prop", "eval-status", "--profile", "trade_the_pool_25k_beginner", "--start", "2026-01-04", "--out", "empty.json"],
    )
    assert res.exit_code == 0, res.output
    report_path = tmp_path / "empty.json"
    with report_path.open("r", encoding="utf-8") as f:
        report = json.load(f)
    # No outcomes: total pnl zero, best trade ratio None, no warnings
    assert report["realised_total_pnl_usd"] == 0.0
    assert report["best_trade_ratio"] is None
    warnings = report.get("warnings", {})
    assert warnings.get("best_trade_concentration") is False
    assert warnings.get("max_drawdown_breached") is False
    assert warnings.get("daily_loss_violations") == []