"""Tests for live mode preflight gating in the daily scan.

This module verifies that when running the daily scan in live mode
without the required API credentials, the orchestrator performs a
deterministic preflight check.  The pipeline should be skipped
entirely, a placeholder decision and run record should still be
created, no stack traces should be printed, and the process should
exit with a non‑zero code while listing the missing variables.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from informer.cli import cli


def _clear_live_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove all environment variables required for live operation.

    This helper deletes the keys validated by the live preflight to
    simulate an environment where no credentials are present.  Missing
    keys are removed silently without raising KeyError.
    """
    for var in [
        "ALPACA_API_KEY_ID",
        "ALPACA_API_SECRET_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    ]:
        monkeypatch.delenv(var, raising=False)


def test_live_scan_preflight_skips_pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Daily scan in live mode should fail closed when credentials are missing.

    This test runs `jarvis daily-scan` in live mode with no API keys set.
    It asserts that the command exits with code 2, prints a deterministic
    message listing the missing variables without a traceback, and that
    both the decision and run record artefacts are created.
    """
    # Clear all live credentials
    _clear_live_keys(monkeypatch)
    # Use a temporary SQLite database for isolation
    db_file = tmp_path / "preflight.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    # Change working directory to tmp_path so that artefacts are written here
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    # Initialize the database to avoid migration errors if the pipeline
    # attempted to touch the DB (preflight should skip pipeline anyway)
    init_res = runner.invoke(cli, ["db-init"])
    assert init_res.exit_code == 0, init_res.output

    # Define a run identifier for reproducibility
    run_id = "test_live_missing"
    # Invoke the daily scan in live mode via the CLI
    result = runner.invoke(cli, ["daily-scan", "--run-id", run_id, "--run-mode", "live"])
    # Preflight should cause the command to exit with code 2 (non‑zero)
    assert result.exit_code == 2, result.output
    # The output should not contain a Python traceback
    assert "Traceback" not in result.output, "Unexpected traceback in output"
    # Decision and run artefact files should have been written
    decision_path = tmp_path / "artifacts" / "decisions" / f"{run_id}.json"
    run_record_path = tmp_path / "artifacts" / "runs" / f"{run_id}.json"
    assert decision_path.exists(), f"Decision file not found: {decision_path}"
    assert run_record_path.exists(), f"Run record file not found: {run_record_path}"
    # The output should list all missing variables.  Since neither Gemini nor
    # Google keys are set, the message reports GEMINI_API_KEY deterministically.
    expected_missing = [
        "ALPACA_API_KEY_ID",
        "ALPACA_API_SECRET_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
    ]
    for var in expected_missing:
        assert var in result.output, f"Missing variable {var} not reported"