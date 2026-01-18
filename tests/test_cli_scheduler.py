"""Tests for the scheduler CLI enhancements.

These tests verify that the scheduler command prints next run times in
multiple timezones and passes the run mode through to the daily scan.
The tests monkeypatch the ``compute_next_run`` function to return a
deterministic datetime so that the output can be validated.  They
avoid sleeping or executing subprocesses by using the ``--dry-run``
option.  No network calls are made.
"""

from __future__ import annotations

from datetime import datetime, timezone

from click.testing import CliRunner

from informer.cli import cli


def _format_expected(dt: datetime) -> tuple[str, str, str]:
    """Return expected NY/UTC/SGT strings for a given UTC datetime.

    This helper converts the input UTC datetime to America/New_York,
    UTC and Asia/Singapore and formats them consistently with the
    scheduler output.  It is used in tests to compute the expected
    substrings that should appear in the CLI output.
    """
    from zoneinfo import ZoneInfo

    ny = dt.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M:%S %Z")
    utc_str = dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    sgt = dt.astimezone(ZoneInfo("Asia/Singapore")).strftime("%Y-%m-%d %H:%M:%S %Z")
    return ny, utc_str, sgt


def test_scheduler_dry_run_prints_times_and_command(monkeypatch) -> None:
    """scheduler --dry-run should print NY/UTC/SGT times and the default command."""
    # Deterministic next run: 2026-01-15 15:15 UTC
    next_run = datetime(2026, 1, 15, 15, 15, tzinfo=timezone.utc)
    # Monkeypatch compute_next_run to return our deterministic value
    monkeypatch.setattr(
        "informer.orchestration.scheduler.compute_next_run",
        lambda now=None, tz_name=None: next_run,
    )
    # Invoke scheduler CLI with dry-run
    runner = CliRunner()
    result = runner.invoke(cli, ["scheduler", "--dry-run"])
    assert result.exit_code == 0, result.output
    ny, utc_str, sgt = _format_expected(next_run)
    # Ensure all three timezone strings are in the output
    for ts in [ny, utc_str, sgt]:
        assert ts in result.output, f"Expected timestamp {ts} missing in output"
    # Ensure the command to be run is printed with default run-mode shadow
    assert "jarvis daily-scan --run-mode shadow" in result.output


def test_scheduler_dry_run_includes_run_mode_live(monkeypatch) -> None:
    """scheduler --dry-run --run-mode live should include run-mode live in command."""
    # Use the same deterministic next run time
    next_run = datetime(2026, 1, 15, 15, 15, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "informer.orchestration.scheduler.compute_next_run",
        lambda now=None, tz_name=None: next_run,
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["scheduler", "--dry-run", "--run-mode", "live"])
    assert result.exit_code == 0, result.output
    ny, utc_str, sgt = _format_expected(next_run)
    for ts in [ny, utc_str, sgt]:
        assert ts in result.output
    # Command should include --run-mode live
    assert "jarvis daily-scan --run-mode live" in result.output