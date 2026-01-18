"""Tests for the JARVIS config-check CLI command.

This module verifies that the config-check command behaves as expected
in both shadow and live modes.  In shadow mode the command should
always exit successfully regardless of which environment variables are
set.  In live mode the command should exit with a non‑zero status
when required environment variables are missing and report which
variables are absent.
"""

from __future__ import annotations

from click.testing import CliRunner

import pytest

from informer.cli import cli


def _clear_config_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Helper to remove all API configuration variables from the environment.

    This function deletes the keys that are validated by the config‑check
    command.  It uses raising=False so that missing keys do not cause
    KeyError.
    """
    for var in [
        "ALPACA_API_KEY_ID",
        "ALPACA_API_SECRET_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    ]:
        monkeypatch.delenv(var, raising=False)


def test_config_check_shadow_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """config-check in shadow mode should always exit with code 0.

    This test clears all relevant environment variables and invokes the
    command in shadow mode.  It asserts that the exit code is zero and
    that the output contains an OK message.
    """
    _clear_config_vars(monkeypatch)
    runner = CliRunner()
    result = runner.invoke(cli, ["config-check", "--mode", "shadow"])
    assert result.exit_code == 0, result.output
    # The output should indicate that keys are not required in shadow mode
    assert "OK" in result.output


def test_config_check_live_missing_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """config-check in live mode should detect missing keys.

    This test clears all required environment variables and invokes the
    command in live mode.  It expects a non‑zero exit code and that
    the names of the missing variables appear in the output.
    """
    _clear_config_vars(monkeypatch)
    runner = CliRunner()
    result = runner.invoke(cli, ["config-check", "--mode", "live"])
    # Exit code should be non‑zero (config check fails)
    assert result.exit_code != 0
    # The output must list all missing variable names deterministically
    for var in [
        "ALPACA_API_KEY_ID",
        "ALPACA_API_SECRET_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
    ]:
        assert var in result.output