"""Tests for the daily scan orchestrator.

These tests ensure that the ``run_daily_scan`` function produces a
decision file even when upstream commands fail.  The runner is
injected to simulate failures without spawning subprocesses.
"""

import json
import os
from pathlib import Path

import pytest

from informer.orchestration.daily_scan import run_daily_scan


def test_daily_scan_creates_decision_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Even if all steps fail, the daily scan writes a decision JSON file."""
    calls = []

    def stub_runner(args):
        # Record commands invoked for debugging
        calls.append(list(args))
        # Return non‑zero to simulate failure
        return 1

    # Run inside a temporary directory to avoid polluting the repository
    monkeypatch.chdir(tmp_path)
    run_id = "test123"
    as_of = "2026-01-01T00:00:00Z"
    run_daily_scan(run_id=run_id, as_of=as_of, run_mode="shadow", runner=stub_runner)
    # Verify that the decision file exists
    decision_file = tmp_path / "artifacts" / "decisions" / f"{run_id}.json"
    assert decision_file.exists()
    # Load and validate the JSON contents
    with decision_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["run_id"] == run_id
    # The status should be "FAILED" because our stub runner always returns an error
    assert data["status"] == "FAILED"
    # Ensure at least the decide command was attempted
    assert ["decide", "--run-id", run_id, "--as-of", as_of] in calls


def test_daily_scan_offline_fast_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """In shadow mode without Alpaca credentials the pipeline should short‑circuit.

    When running in shadow mode and the Alpaca API key ID or secret key is
    missing, the orchestrator should skip ingest and downstream steps,
    immediately emit a placeholder decision, and not invoke any commands via
    the default runner.  This test patches the default runner to record
    invocation attempts so we can assert that no commands were executed.
    """
    calls: list[list[str]] = []

    # Define a stub runner that records any attempted commands.
    def stub_runner(args: list[str]) -> int:
        calls.append(list(args))
        return 0

    # Patch the _default_runner in the daily_scan module to our stub.  The
    # offline fast‑path is triggered only when runner is None, so by
    # replacing the default runner we can detect if any commands would have
    # been invoked had the pipeline proceeded.
    import informer.orchestration.daily_scan as ds

    monkeypatch.setattr(ds, "_default_runner", stub_runner)

    # Ensure Alpaca credentials are absent from the environment.  Deleting
    # these variables triggers the offline shortcut in shadow mode.
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)

    # Change into a temporary directory so that artifacts are written there.
    monkeypatch.chdir(tmp_path)

    run_id = "offline_run"
    as_of = "2026-01-02T00:00:00Z"
    # Invoke run_daily_scan without specifying a custom runner.  This
    # triggers the offline fast‑path when credentials are missing.
    ds.run_daily_scan(run_id=run_id, as_of=as_of, run_mode="shadow", runner=None)

    # The decision file should exist and contain a NOT_READY action.
    decision_file = tmp_path / "artifacts" / "decisions" / f"{run_id}.json"
    assert decision_file.exists()
    with decision_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    # The orchestrator should have written a placeholder decision with
    # NOT_READY/FAILED status when skipping the pipeline.
    assert data.get("action") in {"NOT_READY", "NO_TRADE"}
    assert data.get("status") == "FAILED"
    # In offline fast‑path shadow mode the orchestrator should
    # short‑circuit all pipeline steps and directly record a
    # forward‑test entry.  Verify that no pipeline commands were
    # invoked and that exactly one command was executed: the forwardtest
    # recording.  The call signature begins with ["forwardtest", "record"].
    assert len(calls) == 1
    # The first two arguments correspond to the forwardtest
    # subcommand and action.  Use slicing to ignore additional flags.
    assert calls[0][:2] == ["forwardtest", "record"]