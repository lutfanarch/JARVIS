"""Tests for valid/invalid/unknown profit accounting in prop eval-status.

These tests verify that the evaluation status command correctly classifies
profitable outcomes into valid, invalid and unknown buckets based on
minimum profit per share and trade duration rules.  It also checks that
the best trade ratio on valid profits drives the concentration warning
according to the profile’s ``max_position_profit_ratio``.

The scenario includes four trades:

1. **Valid win** – profit per share ≥ $0.10 and duration ≥ 30 s.
2. **Invalid win** – profit per share < $0.10 (duration ok).
3. **Unknown validity win** – missing duration_seconds field (profit per share ok).
4. **Loss** – negative P&L; should not contribute to any validity bucket but does affect net progress.

For the Trade The Pool 25k beginner profile the per‑trade risk budget is
irrelevant here because we craft the outcomes manually.  The test
ensures that only the valid profit contributes to the valid profit sum
and that the concentration ratio is computed on the valid profits.  A
ratio of 1.0 (100%) against a profile limit of 30% triggers the
``best_trade_concentration`` warning.  The presence of invalid and
unknown profits also raises the respective warning flags.
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
    duration_seconds: int | None = None,
) -> None:
    """Helper to append an outcome record to forward_test_outcomes.jsonl.

    Allows specifying a duration_seconds field.  Only writes the
    duration when not ``None`` to mirror the CLI behaviour.
    """
    outcomes_file = base_dir / "artifacts" / "forward_test" / "forward_test_outcomes.jsonl"
    outcomes_file.parent.mkdir(parents=True, exist_ok=True)
    record: dict[str, object] = {
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


def test_validity_profit_buckets_and_warnings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Evaluation status should classify profits and raise warnings appropriately.

    Creates four trades: one valid win, one invalid win, one unknown
    validity win (missing duration), and one loss.  Checks that the
    resulting report includes correct sums for valid, invalid and unknown
    profit buckets and that the best trade ratio on valid profits
    triggers the concentration warning.  Also ensures that invalid and
    unknown profits set their respective warning flags.
    """
    # Define a common NY date
    date = "2026-01-10"
    # Valid win: profit per share = 1.0 (>=0.10), duration = 40 >= 30; profit = (11-10)*50 = 50
    _create_run(
        tmp_path,
        run_id="run1",
        ny_date=date,
        created_at="2026-01-10T10:00:00Z",
        symbol="AAPL",
        entry=10.0,
        stop=9.0,
        shares=50,
    )
    _append_outcome(
        tmp_path,
        ny_date=date,
        symbol="AAPL",
        entry=10.0,
        exit=11.0,
        notes="valid win",
        recorded_at="2026-01-10T10:30:00Z",
        duration_seconds=40,
    )
    # Invalid win: profit per share = 0.05 (<0.10), duration ok; profit = (20.05-20)*50 = 2.5
    _create_run(
        tmp_path,
        run_id="run2",
        ny_date=date,
        created_at="2026-01-10T11:00:00Z",
        symbol="TSLA",
        entry=20.0,
        stop=19.0,
        shares=50,
    )
    _append_outcome(
        tmp_path,
        ny_date=date,
        symbol="TSLA",
        entry=20.0,
        exit=20.05,
        notes="invalid win",
        recorded_at="2026-01-10T11:30:00Z",
        duration_seconds=40,
    )
    # Unknown validity win: profit per share = 1.0, but duration missing; profit = (31-30)*50 = 50
    _create_run(
        tmp_path,
        run_id="run3",
        ny_date=date,
        created_at="2026-01-10T12:00:00Z",
        symbol="GOOGL",
        entry=30.0,
        stop=29.0,
        shares=50,
    )
    _append_outcome(
        tmp_path,
        ny_date=date,
        symbol="GOOGL",
        entry=30.0,
        exit=31.0,
        notes="unknown validity win",
        recorded_at="2026-01-10T12:30:00Z",
        duration_seconds=None,
    )
    # Loss: negative profit; should not count towards any profit buckets
    _create_run(
        tmp_path,
        run_id="run4",
        ny_date=date,
        created_at="2026-01-10T13:00:00Z",
        symbol="AMZN",
        entry=40.0,
        stop=39.0,
        shares=50,
    )
    _append_outcome(
        tmp_path,
        ny_date=date,
        symbol="AMZN",
        entry=40.0,
        exit=39.0,
        notes="loss",
        recorded_at="2026-01-10T13:30:00Z",
        duration_seconds=None,
    )
    # Change working directory to temp path
    monkeypatch.chdir(tmp_path)
    # Invoke eval-status
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "prop",
            "eval-status",
            "--profile",
            "trade_the_pool_25k_beginner",
            "--start",
            date,
            "--out",
            "report_validity.json",
        ],
    )
    assert result.exit_code == 0, result.output
    report_path = tmp_path / "report_validity.json"
    with report_path.open("r", encoding="utf-8") as f:
        report = json.load(f)
    # Check net realised PnL: 50 + 2.5 + 50 + (-50) = 52.5
    assert abs(report["realised_total_pnl_usd"] - 52.5) < 1e-6
    # Valid profit sum should equal 50
    assert abs(report["valid_profit_usd"] - 50.0) < 1e-6
    # Invalid profit sum should equal 2.5
    assert abs(report["invalid_profit_usd"] - 2.5) < 1e-6
    # Unknown validity profit sum should equal 50
    assert abs(report["unknown_validity_profit_usd"] - 50.0) < 1e-6
    # Best valid trade profit should be 50
    assert abs(report["best_trade_valid_profit_usd"] - 50.0) < 1e-6
    # Best trade ratio on valid profits should be 1.0
    # Use None comparison guard in case of floating rounding
    assert report["best_trade_ratio_valid_profit"] is not None
    assert abs(report["best_trade_ratio_valid_profit"] - 1.0) < 1e-6
    # Warning flags: best trade concentration, invalid profit and unknown duration should all be True
    warnings = report.get("warnings", {})
    assert warnings.get("best_trade_concentration") is True
    assert warnings.get("invalid_profit_present") is True
    assert warnings.get("unknown_duration_present") is True
    # Drawdown and daily loss warnings should not be triggered in this scenario
    assert warnings.get("max_drawdown_breached") is False
    assert warnings.get("daily_loss_violations") == []