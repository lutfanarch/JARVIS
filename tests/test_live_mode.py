"""Tests for live LLM mode initialisation and fail‑safe behaviour.

These tests verify that when the environment is configured for live
LLM mode but required API keys are missing, the decide command
performs a deterministic preflight check and fails closed with a
``NOT_READY`` decision.  No pipeline or client initialisation
occurs, and the CLI exits with a non‑zero status.  The decision
should include a ``MISSING_API_KEYS`` reason code and list the
missing variables in its audit field.  No live network calls are
attempted and no stack trace is emitted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from informer.cli import cli


def test_decide_live_mode_missing_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When LLM_MODE=live and required API keys are missing, decide should fail closed with NOT_READY."""
    # Ensure live mode is requested
    monkeypatch.setenv("LLM_MODE", "live")
    # Clear all API keys to simulate missing credentials
    for var in [
        "ALPACA_API_KEY_ID",
        "ALPACA_API_SECRET_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    ]:
        monkeypatch.delenv(var, raising=False)
    # Restrict symbols to a single allowed symbol to simplify packet loading
    monkeypatch.setenv("SYMBOLS", "AAPL")
    # Create empty packets directory (no packets required since preflight skips pipeline)
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
    # The preflight should exit with code 2 (non‑zero)
    assert result.exit_code == 2, result.output
    # Ensure no traceback is printed
    assert "Traceback" not in result.output, "Unexpected traceback in output"
    # Decision file should exist
    decision_file = decisions_dir / "test_run.json"
    assert decision_file.exists(), f"Decision file not found: {decision_file}"
    with decision_file.open("r", encoding="utf-8") as f:
        decision = json.load(f)
    # Expect a NOT_READY decision
    assert decision.get("action") == "NOT_READY"
    # Reason codes should include MISSING_API_KEYS
    rcodes = decision.get("reason_codes", [])
    assert "MISSING_API_KEYS" in rcodes
    # Audit should include missing_env_vars list
    audit = decision.get("audit", {})
    assert "missing_env_vars" in audit
    missing = audit.get("missing_env_vars")
    # Should include all required variables (Gemini key stands for either Gemini or Google)
    # The missing variables list returned by the implementation should already be
    # sorted lexicographically.  Define the expected order explicitly and
    # compare directly.
    expected_missing = [
        "ALPACA_API_KEY_ID",
        "ALPACA_API_SECRET_KEY",
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
    ]
    assert missing == expected_missing