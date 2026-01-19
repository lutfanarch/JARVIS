"""Tests for the verify‑dod‑b command.

This module exercises the new ``jarvis verify-dod-b`` CLI command.  The
tests focus on deterministic behavior: a report must always be
produced, the command must exit with the correct code based on
success/failure, and the help text must include a description of the
verification.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from informer.cli import cli


def test_verify_dod_b_wrong_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When invoked outside of a repository, verify-dod-b writes a report and exits 2."""
    # Change into an empty temporary directory (no src/, tests/, pyproject.toml)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    # Run the command with skip-pytest to avoid running the suite in a non-repo
    result = runner.invoke(cli, ["verify-dod-b", "--run-id", "test_shipit", "--skip-pytest"])
    # The command should exit with status 2 indicating at least one failure
    assert result.exit_code == 2, result.output
    # The report file must still be created under artifacts/shipit
    report_file = tmp_path / "artifacts" / "shipit" / "test_shipit.json"
    assert report_file.exists(), "Report JSON not created on failure"
    with report_file.open("r", encoding="utf-8") as f:
        report = json.load(f)
    # overall_pass should be False because the repo sanity check fails
    assert report.get("overall_pass") is False
    # There should be a repo_sanity step in the report
    names = [step.get("name") for step in report.get("steps", [])]
    assert "repo_sanity" in names


def test_verify_dod_b_help() -> None:
    """The verify‑dod‑b command should provide helpful usage text."""
    runner = CliRunner()
    result = runner.invoke(cli, ["verify-dod-b", "--help"])
    # Help should exit successfully
    assert result.exit_code == 0
    # The help output should mention verifying DoD or definition of done
    output = result.output.lower()
    assert "verify" in output and ("dod" in output or "definition of done" in output)


def test_verify_dod_b_runs_in_repo_and_invokes_informer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """verify-dod-b should run using ``python -m informer`` for CLI invocations.

    This test constructs a minimal repository structure and monkeypatches
    ``subprocess.run`` so that the verify-dod-b command executes quickly and
    deterministically.  It verifies that the command exits with code 0 when
    all steps succeed and that CLI invocations use ``python -m informer``
    rather than the deprecated ``informer.cli`` entry point.
    """
    # Set up a minimal repo: create pyproject.toml, src/ and tests/ directories
    (tmp_path / "pyproject.toml").touch()
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    # Track commands executed via subprocess.run
    executed_commands: list[list[str]] = []

    # Fake subprocess.run to capture commands and emulate successful execution
    def fake_run(cmd, env=None, capture_output=True, text=True, timeout=None):  # type: ignore[override]
        executed_commands.append(cmd)
        # Determine return code: config-check live returns non-zero; others succeed
        rc = 0
        stdout = ""
        stderr = ""
        if any(str(part) == "config-check" for part in cmd) and any(str(part) == "live" for part in cmd):
            rc = 2
            stderr = "Missing env vars"
        # When daily-scan is invoked, create the required artifact files
        if any(str(part) == "daily-scan" for part in cmd):
            # Extract the run-id value following the --run-id flag
            run_id = None
            for i, part in enumerate(cmd):
                if part == "--run-id" and i + 1 < len(cmd):
                    run_id = cmd[i + 1]
                    break
            if run_id:
                base = tmp_path
                decisions_dir = base / "artifacts" / "decisions"
                runs_dir = base / "artifacts" / "runs"
                ft_dir = base / "artifacts" / "forward_test"
                decisions_dir.mkdir(parents=True, exist_ok=True)
                runs_dir.mkdir(parents=True, exist_ok=True)
                ft_dir.mkdir(parents=True, exist_ok=True)
                # Create decision and run files
                (decisions_dir / f"{run_id}.json").write_text("{}", encoding="utf-8")
                (runs_dir / f"{run_id}.json").write_text("{}", encoding="utf-8")
                # Append run-id to forward_test_runs.jsonl
                ft_file = ft_dir / "forward_test_runs.jsonl"
                ft_file.write_text(f"{{\"run_id\": \"{run_id}\"}}\n", encoding="utf-8")
        # Use SimpleNamespace to mimic CompletedProcess
        from types import SimpleNamespace
        return SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)

    # Monkeypatch subprocess.run used in the CLI implementation
    import subprocess as sp
    monkeypatch.setattr(sp, "run", fake_run)
    # Change directory into the tmp repo
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["verify-dod-b", "--run-id", "test_shipit", "--skip-pytest"])
    # The command should succeed because the fake subprocess marks all steps as passing
    assert result.exit_code == 0, result.output
    # At least one executed command should use ``-m informer`` and none should use ``informer.cli``
    flattened = [" ".join(cmd) for cmd in executed_commands]
    assert any("-m informer" in cmd for cmd in flattened), f"Expected '-m informer' in commands: {flattened}"
    assert not any("informer.cli" in cmd for cmd in flattened), f"Unexpected 'informer.cli' in commands: {flattened}"