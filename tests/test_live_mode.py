"""Tests for live LLM mode initialisation and fail‑safe behaviour.

These tests verify that when the environment is configured for live
LLM mode but required API keys are missing, the decide command
produces a deterministic no‑trade decision rather than falling back to
the fake client.  The decision should contain reason codes
``LIVE_MODE_INIT_FAILED`` and ``MISSING_API_KEYS`` and include the
initialisation error in the audit field.  No live network calls are
attempted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from informer.cli import cli


def test_decide_live_mode_missing_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When LLM_MODE=live and API keys are missing, decide should yield a no‑trade decision."""
    # Ensure live mode is requested
    monkeypatch.setenv("LLM_MODE", "live")
    # Unset any API keys to simulate missing credentials
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    # Restrict symbols to a single allowed symbol to simplify packet loading
    monkeypatch.setenv("SYMBOLS", "AAPL")
    # Create empty packets directory (no packets required since init failure short‑circuits pipeline)
    packets_dir = tmp_path / "packets"
    packets_dir.mkdir(parents=True, exist_ok=True)
    # Output directory for decision
    decisions_dir = tmp_path / "decisions"
    # Run decide via CLI
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "decide",
            "--symbols",
            "AAPL",
            "--packets-dir",
            str(packets_dir),
            "--out-dir",
            str(decisions_dir),
            "--run-id",
            "test_run",
            "--as-of",
            "2025-01-03T15:00:00Z",
            "--max-risk-usd",
            "50",
        ],
    )
    assert result.exit_code == 0, result.output
    # Decision file should exist
    decision_file = decisions_dir / "test_run.json"
    assert decision_file.exists(), f"Decision file not found: {decision_file}"
    with decision_file.open("r", encoding="utf-8") as f:
        decision = json.load(f)
    # Expect a no‑trade decision
    assert decision.get("action") == "NO_TRADE"
    # Reason codes should include our live mode failure codes
    rcodes = decision.get("reason_codes", [])
    assert "LIVE_MODE_INIT_FAILED" in rcodes
    assert "MISSING_API_KEYS" in rcodes
    # Audit should include the init error
    audit = decision.get("audit", {})
    assert "live_init_error" in audit