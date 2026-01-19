"""Tests for live mode preflight gating in the decide CLI command.

This module verifies that when running the decide command in live LLM mode
without the required API credentials, the CLI performs a deterministic
preflight check.  The pipeline should be skipped entirely, a NOT_READY
decision should be emitted, no stack traces should be printed, and the
process should exit with a non‑zero code while listing the missing
variables.  The resulting decision file must exist and contain the
appropriate action.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from informer.cli import cli


def _clear_live_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove all environment variables required for live LLM operation.

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


def test_decide_live_preflight_emits_not_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """decide in live LLM mode should fail closed when credentials are missing.

    This test runs `jarvis decide` in live mode with no API keys set.  It asserts
    that the command exits with code 2, prints a deterministic message listing
    the missing variables without a traceback, and that the decision artefact
    is created with action NOT_READY.  The report should include the missing
    variable names sorted.
    """
    # Clear all live credentials
    _clear_live_keys(monkeypatch)
    # Set LLM_MODE to live to trigger preflight
    monkeypatch.setenv("LLM_MODE", "live")
    # Provide a simple whitelist via environment to avoid whitelist errors
    monkeypatch.setenv("SYMBOLS", "AAPL")
    # Create an empty packets directory
    packets_dir = tmp_path / "packets"
    packets_dir.mkdir(parents=True, exist_ok=True)
    # Create an output directory for decisions
    out_dir = tmp_path / "decisions"
    # Define a run identifier
    run_id = "test_decide_live_missing"
    # Run the decide CLI
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "decide",
            "--run-id",
            run_id,
            "--packets-dir",
            str(packets_dir),
            "--out-dir",
            str(out_dir),
            "--symbols",
            "AAPL",
        ],
    )
    # Preflight should cause the command to exit with code 2 (non‑zero)
    assert result.exit_code == 2, result.output
    # The output should not contain a Python traceback
    assert "Traceback" not in result.output, "Unexpected traceback in output"
    # Decision artefact file should have been written
    decision_path = out_dir / f"{run_id}.json"
    assert decision_path.exists(), f"Decision file not found: {decision_path}"
    # Load the decision JSON and verify action is NOT_READY
    with decision_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    assert data.get("action") == "NOT_READY", f"Expected NOT_READY decision, got {data.get('action')}"
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