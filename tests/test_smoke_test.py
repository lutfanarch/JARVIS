"""Tests for the smoke test command.

These tests verify that the ``jarvis smoke-test`` command runs
successfully in shadow mode without requiring network credentials and
produces a decision artifact.  The test uses a temporary working
directory to ensure that any artifacts are isolated from the
repository.  Alpaca credentials are explicitly removed from the
environment to trigger the offline fast path in the daily scan.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
import pytest

from informer.cli import cli


def test_smoke_test_creates_decision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The smoke test should exit 0 and write a decision file in shadow mode.

    This test runs ``jarvis smoke-test`` in a temporary directory with
    Alpaca credentials unset.  It asserts that the command succeeds
    (exit code 0) and that a decision artifact exists under
    ``artifacts/decisions/<run_id>.json``.  The run ID is set
    explicitly to simplify the expected filename.
    """
    # Use a deterministic run ID for the smoke test
    run_id = "test_smoke"
    # Set working directory to the tmp_path
    monkeypatch.chdir(tmp_path)
    # Ensure Alpaca credentials are absent to trigger offline fast path
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
    # Prepare a SQLite path in the temp directory
    db_path = tmp_path / "smoke.db"
    # Invoke the CLI using CliRunner
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "smoke-test",
            "--db-path",
            str(db_path),
            "--run-id",
            run_id,
            # Do not specify --keep so that the DB file is removed after test
        ],
    )
    # Command should succeed (exit code 0)
    assert result.exit_code == 0, result.output
    # Decision file must exist
    decision_file = tmp_path / "artifacts" / "decisions" / f"{run_id}.json"
    assert decision_file.exists(), "Smoke test did not create decision artifact"
    # Load and verify the decision JSON contains the expected run_id
    with decision_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    assert data.get("run_id") == run_id