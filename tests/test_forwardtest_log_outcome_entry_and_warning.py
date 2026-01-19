"""Tests for derived entry and duration warnings in forwardtest log‑outcome.

This module verifies additional behaviours for the ``forwardtest log-outcome``
command introduced in ticket 18.22:

1. When logging an outcome via ``--run-id`` and no explicit ``--entry``
   parameter is provided, the command should attempt to derive the realised
   entry price from the corresponding ``decision.json`` artifact.  If the
   decision file is present and contains a numeric ``entry`` field, the
   outcome record should include this value.  The command remains
   deterministic even if the file is missing or malformed.

2. When a profitable outcome is logged (``exit`` > ``entry``) and no
   ``--duration-seconds`` is supplied, the CLI should emit a deterministic
   warning reminding the user that duration is required for valid profit
   evaluation.  The presence or absence of the warning depends on whether
   the entry price is known.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from informer.cli import cli


def _setup_registry_with_run(tmpdir: Path, run_id: str, ny_date: str, symbol: str) -> None:
    """Create a single TRADE run in the forward-test registry.

    The registry entry has the provided run_id, trade date and symbol and
    references an artifact directory at the canonical forward_test path.
    """
    ft_dir = tmpdir / "artifacts" / "forward_test"
    ft_dir.mkdir(parents=True, exist_ok=True)
    runs_path = ft_dir / "forward_test_runs.jsonl"
    record = {
        "run_id": run_id,
        "ny_date": ny_date,
        "created_at_utc": f"{ny_date}T00:00:00Z",
        "mode": "shadow",
        "symbols": [symbol],
        "decision_status": "TRADE",
        "selected_symbol": symbol,
        "rationale_summary": None,
        "schema_version": None,
        "config_hash": f"hash-{run_id}",
        "artifact_dir": f"artifacts/forward_test/{ny_date}/{run_id}",
        "lock_key": f"lock-{run_id}",
    }
    with runs_path.open("w", encoding="utf-8") as f:
        json.dump(record, f, sort_keys=True)
        f.write("\n")


def test_derive_entry_and_warn_on_missing_duration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Using --run-id without entry should derive entry from decision.json and warn when profitable without duration."""
    # Setup a registry entry for run r1
    run_id = "r1"
    ny_date = "2026-02-01"
    symbol = "AAPL"
    _setup_registry_with_run(tmp_path, run_id, ny_date, symbol)
    # Create decision.json with an entry price in the referenced artifact directory
    artifact_dir = tmp_path / "artifacts" / "forward_test" / ny_date / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    decision_path = artifact_dir / "decision.json"
    decision_data = {"entry": 100.0}
    with decision_path.open("w", encoding="utf-8") as f:
        json.dump(decision_data, f)
    # Change working directory to temp path
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    # Log outcome without explicit entry or duration; should derive entry and warn
    result = runner.invoke(
        cli,
        ["forwardtest", "log-outcome", "--run-id", run_id, "--exit", "101.0"],
    )
    # Command should succeed
    assert result.exit_code == 0, result.output
    # Verify that a warning about missing duration_seconds appears
    assert "profitable outcome logged without duration_seconds" in result.output
    # Outcome file should exist and include derived entry
    outcomes_path = tmp_path / "artifacts" / "forward_test" / "forward_test_outcomes.jsonl"
    assert outcomes_path.exists(), "outcomes file was not created"
    with outcomes_path.open("r", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]
    assert len(lines) == 1
    rec = lines[0]
    # Confirm derived entry present
    assert abs(rec.get("entry") - 100.0) < 1e-6
    # Duration should be omitted
    assert "duration_seconds" not in rec


def test_no_warning_when_entry_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No warning should be emitted when entry cannot be determined."""
    # Setup a registry entry for run r2 without any decision artifact
    run_id = "r2"
    ny_date = "2026-02-02"
    symbol = "MSFT"
    _setup_registry_with_run(tmp_path, run_id, ny_date, symbol)
    # Do not create decision.json => entry will remain None
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    # Invoke log-outcome; no entry and no duration
    result = runner.invoke(
        cli,
        ["forwardtest", "log-outcome", "--run-id", run_id, "--exit", "105.0"],
    )
    # Should succeed without warning
    assert result.exit_code == 0, result.output
    assert "profitable outcome logged without duration_seconds" not in result.output
    # Load outcome
    outcomes_path = tmp_path / "artifacts" / "forward_test" / "forward_test_outcomes.jsonl"
    with outcomes_path.open("r", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]
    assert len(lines) == 1
    rec = lines[0]
    # Entry should be absent
    assert "entry" not in rec or rec.get("entry") is None
    # Duration key absent
    assert "duration_seconds" not in rec