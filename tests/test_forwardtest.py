"""Tests for forward-test shadow mode and registry functionality.

These tests exercise the forwardtest CLI commands and verify that
shadow mode suppresses notifications, records runs to the registry,
respects the one-trade-per-day lock and produces summary reports.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from informer.cli import cli


def _make_trade_decision(symbol: str = "AAPL") -> dict:
    """Build a simple trade decision dictionary for testing."""
    return {
        "run_id": "test_run",
        "generated_at": "2025-01-02T00:00:00Z",
        "as_of": "2025-01-02T00:00:00Z",
        "whitelist": [symbol],
        "max_risk_usd": 50.0,
        "action": "TRADE",
        "trade_date_ny": "2025-01-02",
        "symbol": symbol,
        "entry": 100.0,
        "stop": 95.0,
        "targets": [110.0],
        "shares": 10,
        "risk_usd": 50.0,
        "r_multiple": 2.0,
        "confidence": 0.9,
        "reason_codes": [],
        "audit": {},
        "decision_schema_version": "1.0",
    }


def test_shadow_mode_suppresses_notification(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Notify command should not send Telegram messages in shadow mode."""
    # Prepare a trade decision file
    decision = _make_trade_decision()
    decision_path = tmp_path / "decision.json"
    with decision_path.open("w", encoding="utf-8") as f:
        json.dump(decision, f)
    # Record calls to telegram.send_message
    calls = []

    def fake_send(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((args, kwargs))
        return True

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dummy")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_ALLOWLIST", "123")
    monkeypatch.setenv("JARVIS_RUN_MODE", "shadow")
    monkeypatch.setattr("informer.notify.telegram.send_message", fake_send)
    runner = CliRunner()
    result = runner.invoke(cli, ["notify", "--decision-file", str(decision_path)])
    assert result.exit_code == 0
    # In shadow mode the notification should be suppressed
    assert not calls


def test_forwardtest_record_creates_artifacts_and_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The forwardtest record command writes standard artefacts and updates the registry."""
    # Change working directory to the temporary path to isolate artefacts
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        # Prepare a decision file with a trade
        run_id = "run001"
        as_of = "2025-01-06T14:00:00Z"
        decision_dir = Path("artifacts/decisions")
        decision_dir.mkdir(parents=True, exist_ok=True)
        decision_path = decision_dir / f"{run_id}.json"
        with decision_path.open("w", encoding="utf-8") as f:
            json.dump(_make_trade_decision("AAPL"), f)
        # Ensure packets directory exists (empty)
        packets_dir = Path("artifacts/packets") / run_id
        packets_dir.mkdir(parents=True, exist_ok=True)
        # Invoke forwardtest record
        runner = CliRunner()
        result = runner.invoke(cli, ["forwardtest", "record", "--run-id", run_id, "--as-of", as_of])
        assert result.exit_code == 0, result.output
        # Compute NY date for artefacts (Monday 2025-01-06)
        ny_date = "2025-01-06"
        forward_root = Path("artifacts/forward_test") / ny_date / run_id
        assert forward_root.exists() and forward_root.is_dir()
        # Expect artefact files
        for fname in [
            "run_config.json",
            "readiness.json",
            "informer_packet.json",
            "decision.json",
            "validator_report.json",
            "lock_status.json",
        ]:
            assert (forward_root / fname).exists(), f"missing {fname}"
        # Check registry entry
        registry_path = Path("artifacts/forward_test/forward_test_runs.jsonl")
        assert registry_path.exists(), "registry file missing"
        with registry_path.open("r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        assert any(entry["run_id"] == run_id for entry in lines), "run entry not recorded"
    finally:
        os.chdir(cwd)


def test_forwardtest_lock_status(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Forward-test record should reflect lock status when an existing trade lock is present."""
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        run_id1 = "runA"
        run_id2 = "runB"
        ny_date = "2025-01-07"
        as_of = "2025-01-07T14:00:00Z"
        # Create a trade lock state representing a prior trade on the same date
        state_dir = Path("artifacts/state")
        state_dir.mkdir(parents=True, exist_ok=True)
        lock_file = state_dir / "trade_lock.json"
        with lock_file.open("w", encoding="utf-8") as f:
            json.dump({"last_trade_date_ny": ny_date, "last_run_id": run_id1}, f)
        # Prepare decision file for second run (NO_TRADE to simulate locked)
        decisions_dir = Path("artifacts/decisions")
        decisions_dir.mkdir(parents=True, exist_ok=True)
        decision2 = _make_trade_decision()
        decision2["run_id"] = run_id2
        decision2["trade_date_ny"] = ny_date
        decision2["action"] = "NO_TRADE"
        decision2_path = decisions_dir / f"{run_id2}.json"
        with decision2_path.open("w", encoding="utf-8") as f:
            json.dump(decision2, f)
        # Ensure packets directory exists
        packets_dir2 = Path("artifacts/packets") / run_id2
        packets_dir2.mkdir(parents=True, exist_ok=True)
        # Record second run
        runner = CliRunner()
        result = runner.invoke(cli, ["forwardtest", "record", "--run-id", run_id2, "--as-of", as_of])
        assert result.exit_code == 0, result.output
        # Check lock_status.json shows lock exists
        fwd_root = Path("artifacts/forward_test") / ny_date / run_id2
        lock_status_file = fwd_root / "lock_status.json"
        assert lock_status_file.exists(), "lock_status.json missing"
        with lock_status_file.open("r", encoding="utf-8") as f:
            lock_status = json.load(f)
        assert lock_status.get("lock_exists") is True, "lock not detected"
        assert lock_status.get("locked_by_run_id") == run_id1, "incorrect locked_by_run_id"
    finally:
        os.chdir(cwd)


def test_forwardtest_report_generates_summary(tmp_path: Path) -> None:
    """forwardtest report should write a JSON summary with counts by status and symbol."""
    # Create a registry file with multiple entries
    registry_dir = tmp_path / "artifacts/forward_test"
    registry_dir.mkdir(parents=True, exist_ok=True)
    reg_path = registry_dir / "forward_test_runs.jsonl"
    entries = [
        {"run_id": "r1", "ny_date": "2025-01-03", "decision_status": "TRADE", "selected_symbol": "AAPL"},
        {"run_id": "r2", "ny_date": "2025-01-04", "decision_status": "NO_TRADE", "selected_symbol": None},
        {"run_id": "r3", "ny_date": "2025-01-05", "decision_status": "TRADE", "selected_symbol": "MSFT"},
    ]
    with reg_path.open("w", encoding="utf-8") as f:
        for e in entries:
            json.dump(e, f)
            f.write("\n")
    # Set cwd to tmp_path for CLI invocation
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        runner = CliRunner()
        report_path = tmp_path / "report.json"
        result = runner.invoke(cli, ["forwardtest", "report", "--start", "2025-01-03", "--end", "2025-01-05", "--out", str(report_path)])
        assert result.exit_code == 0, result.output
        assert report_path.exists(), "report not created"
        with report_path.open("r", encoding="utf-8") as f:
            report = json.load(f)
        assert report.get("total_runs") == 3
        # Expect counts by status and symbol
        assert report["status_counts"].get("TRADE") == 2
        assert report["status_counts"].get("NO_TRADE") == 1
        assert report["symbol_counts"].get("AAPL") == 1
        assert report["symbol_counts"].get("MSFT") == 1
    finally:
        os.chdir(cwd)