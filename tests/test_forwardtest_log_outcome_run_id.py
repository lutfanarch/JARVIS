"""Tests for logging forward-test outcomes by run identifier.

This module exercises the ``forwardtest log-outcome`` CLI when using
the new ``--run-id`` option to derive the trade date and symbol
automatically from the forward-test registry.  It verifies that
outcomes are recorded correctly and that invalid combinations of
options produce deterministic error messages and exit codes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from informer.cli import cli


def _setup_registry_for_log(tmpdir: Path) -> None:
    """Create a minimal forward test registry for outcome logging tests.

    This helper writes two run entries to the forward test registry:

    * A TRADE run with run_id ``r1``, ny_date ``2026-01-15`` and
      selected_symbol ``AAPL``.
    * A non‑TRADE run with run_id ``bad_run``, ny_date ``2026-01-16`` and
      selected_symbol ``AAPL``, but with decision_status ``NOT_READY``.

    The registry is written to ``artifacts/forward_test/forward_test_runs.jsonl``
    under ``tmpdir``.
    """
    ft_dir = tmpdir / "artifacts" / "forward_test"
    ft_dir.mkdir(parents=True, exist_ok=True)
    runs_path = ft_dir / "forward_test_runs.jsonl"
    runs = [
        {
            "run_id": "r1",
            "ny_date": "2026-01-15",
            "created_at_utc": "2026-01-15T00:00:00Z",
            "mode": "shadow",
            "symbols": ["AAPL"],
            "decision_status": "TRADE",
            "selected_symbol": "AAPL",
            "rationale_summary": None,
            "schema_version": None,
            "config_hash": "hash1",
            "artifact_dir": "artifacts/forward_test/2026-01-15/r1",
            "lock_key": "lock1",
        },
        {
            "run_id": "bad_run",
            "ny_date": "2026-01-16",
            "created_at_utc": "2026-01-16T00:00:00Z",
            "mode": "shadow",
            "symbols": ["AAPL"],
            "decision_status": "NOT_READY",
            "selected_symbol": "AAPL",
            "rationale_summary": None,
            "schema_version": None,
            "config_hash": "hash2",
            "artifact_dir": "artifacts/forward_test/2026-01-16/bad_run",
            "lock_key": "lock2",
        },
    ]
    with runs_path.open("w", encoding="utf-8") as f:
        for rec in runs:
            json.dump(rec, f, sort_keys=True)
            f.write("\n")


def test_log_outcome_by_run_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Logging an outcome via --run-id should derive ny_date and symbol and record the outcome."""
    _setup_registry_for_log(tmp_path)
    # Change working directory to temp path
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    # Invoke log-outcome with run-id r1; omit entry
    result = runner.invoke(
        cli,
        ["forwardtest", "log-outcome", "--run-id", "r1", "--exit", "101.0"],
    )
    # Should succeed
    assert result.exit_code == 0, result.output
    # Outcome file should exist with derived date and symbol
    outcomes_path = tmp_path / "artifacts" / "forward_test" / "forward_test_outcomes.jsonl"
    assert outcomes_path.exists(), "outcomes file was not created"
    # Load the single outcome record
    with outcomes_path.open("r", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]
    assert len(lines) == 1
    rec = lines[0]
    # Derived fields
    assert rec.get("ny_date") == "2026-01-15"
    assert rec.get("symbol") == "AAPL"
    # Exit price recorded
    assert abs(rec.get("exit") - 101.0) < 1e-6
    # Entry key should be absent when omitted
    assert "entry" not in rec or rec.get("entry") is None


def test_log_outcome_by_run_id_non_trade_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Logging an outcome for a non‑TRADE run-id should fail with exit code 2."""
    _setup_registry_for_log(tmp_path)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["forwardtest", "log-outcome", "--run-id", "bad_run", "--exit", "101.0"],
    )
    # Command should exit with non-zero (2) and include error about non-TRADE
    assert result.exit_code == 2
    # Check error message mentions that the run is not a TRADE
    assert "not a TRADE" in result.output


def test_log_outcome_run_id_and_explicit_params_conflict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Providing run-id together with ny-date/symbol should fail deterministically."""
    _setup_registry_for_log(tmp_path)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "forwardtest",
            "log-outcome",
            "--run-id",
            "r1",
            "--ny-date",
            "2026-01-15",
            "--symbol",
            "AAPL",
            "--exit",
            "101.0",
        ],
    )
    # Should exit with code 2 and produce a clear error message
    assert result.exit_code == 2
    assert "Do not specify" in result.output