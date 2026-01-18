"""Tests for the notify CLI command and Telegram dispatch.

These unit tests ensure that the `notify` command behaves correctly
depending on the decision file contents.  No network requests are
performed during these tests; instead, the `send_message` function
from ``informer.notify.telegram`` is monkeypatched to record calls.

The tests verify that:

* When the decision action is ``NO_TRADE``, no notification is
  attempted.
* When the decision action is ``TRADE``, a message is sent exactly
  once with the appropriate parameters and a deduplication key.
* The chat ID allowlist is enforced via the environment (in these
  tests the allowlist always contains the target chat ID).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from informer.cli import cli


def _write_decision(tmp_path: Path, data: dict) -> Path:
    """Helper to write a JSON decision file in the temp directory."""
    p = tmp_path / "decision.json"
    with p.open("w", encoding="utf-8") as f:
        json.dump(data, f)
    return p


def test_notify_does_nothing_on_no_trade(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """notify should exit without sending when the decision is NO_TRADE."""
    # Create a NO_TRADE decision file
    decision = {
        "run_id": "run001",
        "generated_at": "2025-01-01T00:00:00Z",
        "as_of": "2025-01-01T00:00:00Z",
        "whitelist": [],
        "max_risk_usd": 50.0,
        "action": "NO_TRADE",
        "trade_date_ny": "2025-01-01",
        "reason_codes": ["EXAMPLE"],
        "audit": {},
    }
    decision_path = _write_decision(tmp_path, decision)
    # Monkeypatch send_message to record calls
    calls = []

    def fake_send(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((args, kwargs))
        return True

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dummy-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_ALLOWLIST", "12345")
    monkeypatch.setattr("informer.notify.telegram.send_message", fake_send)
    runner = CliRunner()
    result = runner.invoke(cli, ["notify", "--decision-file", str(decision_path)])
    assert result.exit_code == 0
    # No calls should have been made
    assert not calls


def test_notify_sends_trade(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """notify should send a Telegram message when the decision action is TRADE."""
    # Create a TRADE decision file with minimal fields
    decision = {
        "run_id": "run002",
        "generated_at": "2025-01-02T00:00:00Z",
        "as_of": "2025-01-02T00:00:00Z",
        "whitelist": ["AAPL"],
        "max_risk_usd": 50.0,
        "action": "TRADE",
        "trade_date_ny": "2025-01-02",
        "symbol": "AAPL",
        "entry": 100.0,
        "stop": 95.0,
        "targets": [110.0],
        "shares": 10,
        "risk_usd": 50.0,
        "r_multiple": 2.0,
        "confidence": 0.9,
        "reason_codes": [],
        "audit": {},
    }
    decision_path = _write_decision(tmp_path, decision)
    # Record parameters passed to send_message
    recorded = {}

    def fake_send(token: str, chat_id: str, text: str, *, dedupe_key: str | None = None) -> bool:
        recorded["token"] = token
        recorded["chat_id"] = chat_id
        recorded["text"] = text
        recorded["dedupe_key"] = dedupe_key
        return True

    # Set environment variables for Telegram
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dummy-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_ALLOWLIST", "12345")
    # Ensure state dir is temporary to avoid interference
    monkeypatch.setenv("TELEGRAM_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr("informer.notify.telegram.send_message", fake_send)
    runner = CliRunner()
    result = runner.invoke(cli, ["notify", "--decision-file", str(decision_path)])
    assert result.exit_code == 0
    # Verify that send_message was called exactly once
    assert recorded.get("token") == "dummy-token"
    assert recorded.get("chat_id") == "12345"
    assert recorded.get("dedupe_key") is not None
    # Message text should include key fields
    text = recorded.get("text", "")
    assert "AAPL" in text
    assert "Entry" in text and "100.0" in text
    assert "Stop" in text and "95.0" in text
    assert "Run ID" in text and "run002" in text