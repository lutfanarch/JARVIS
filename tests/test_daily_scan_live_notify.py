"""Tests for live mode daily scan resilience to notify/config failures.

These tests ensure that when running the daily scan in live mode, a
failure in the notification step does not abort the overall run.  The
pipeline should still exit successfully, write the decision and run
record artefacts, and mark the notify step as FAIL in the run
record.  No network calls are performed in these tests; instead, a
stub runner is injected to simulate step outcomes and the decision
file is pre‑created with a TRADE action.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from informer.orchestration.daily_scan import run_daily_scan


def test_live_daily_scan_notify_failure_does_not_abort(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Live mode should tolerate notify failures and still write artefacts.

    This test creates a fake decision with a TRADE action, sets up a stub
    runner that simulates a failure only on the notify step, and runs
    the daily scan in live mode.  It asserts that the pipeline exits
    without raising exceptions, writes both the decision and run record
    files, and records the notify step as FAIL with a short error.
    """
    # Change working directory to temporary path
    monkeypatch.chdir(tmp_path)
    # Define run_id and as_of timestamp
    run_id = "live_notify_test"
    as_of = "2026-03-01T14:00:00Z"
    # Precreate a decision file with action=TRADE so that the decide step
    # can succeed without invoking a real decision pipeline.  The
    # orchestrator will not overwrite an existing decision file if the
    # decide step is skipped or fails.
    decisions_dir = Path("artifacts/decisions")
    decisions_dir.mkdir(parents=True, exist_ok=True)
    decision_path = decisions_dir / f"{run_id}.json"
    with decision_path.open("w", encoding="utf-8") as f:
        json.dump({
            "run_id": run_id,
            "generated_at": as_of,
            "as_of": as_of,
            "action": "TRADE",
            "trade_date_ny": "2026-03-01",
            "symbol": "AAPL",
            "entry": 100.0,
            "stop": 95.0,
            "targets": [110.0],
            "shares": 1,
            "risk_usd": 5.0,
            "r_multiple": 2.0,
            "confidence": 0.9,
            "reason_codes": [],
            "audit": {},
        }, f)
    # Stub runner: return 0 for all steps except notify (simulate failure)
    calls = []

    def stub_runner(args: list[str]) -> int:
        calls.append(list(args))
        # Simulate failure only for notify step
        if args and args[0] == "notify":
            return 1  # non‑zero exit code
        return 0
    # Ensure environment has Telegram variables missing so notify would fail
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID_ALLOWLIST", raising=False)
    # Run daily scan in live mode with stub runner
    run_daily_scan(run_id=run_id, as_of=as_of, run_mode="live", runner=stub_runner)
    # Verify that decision file still exists and was not overwritten
    assert decision_path.exists(), "Decision file missing after daily scan"
    # Run record file should exist
    run_record_path = Path("artifacts/runs") / f"{run_id}.json"
    assert run_record_path.exists(), "Run record file missing"
    with run_record_path.open("r", encoding="utf-8") as f:
        run_record = json.load(f)
    # Ensure steps list contains notify with FAIL status and a short error
    step_map = {step["name"]: step for step in run_record.get("steps", [])}
    assert "notify" in step_map, "Notify step missing from run record"
    notify_step = step_map["notify"]
    assert notify_step["status"] == "FAIL", "Notify step should be marked FAIL"
    # The short_error should mention the return code (1) but not be a long traceback
    assert notify_step.get("short_error"), "Notify step short_error missing"
    assert "1" in notify_step["short_error"], "Notify short_error should mention return code"
    # The overall pipeline should have recorded other steps; ensure run_mode is live
    assert run_record["run_mode"] == "live"