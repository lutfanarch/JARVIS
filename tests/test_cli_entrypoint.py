"""Test that the jarvis console entrypoint is installed and functional.

This integration test installs the package in editable mode and asserts
that the ``jarvis`` script is available on the PATH.  It then invokes
``jarvis --help`` via subprocess to verify that the command prints
usage information containing the top-level help message and lists
several subcommands.  Note: this test may be skipped in environments
without pip or where editable installs are not permitted.
"""

from __future__ import annotations

import os
import subprocess
import sys


def test_cli_entrypoint_works(tmp_path) -> None:
    """Ensure that the jarvis console entrypoint is installed and usable.

    This test does not blindly reinstall the package.  If the ``jarvis``
    command is already on the PATH, it assumes the editable install has
    been performed by the test runner and skips the installation step.
    Otherwise it installs the package in editable mode using ``pip``
    with ``--no-deps`` to avoid pulling in dependencies that may be
    unavailable in offline environments.  After ensuring the script
    exists, it invokes ``jarvis --help`` with a timeout to prevent
    hanging.  The output must include the top-level CLI description
    and list common subcommands.
    """
    import shutil

    # Check if the jarvis command is already available on the PATH
    jarvis_path = shutil.which("jarvis")
    if jarvis_path is None:
        # Install the project in editable mode if not already installed
        subprocess.run([
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "-e",
            ".",
        ], check=True)
        jarvis_path = shutil.which("jarvis")
        assert jarvis_path is not None, "jarvis entrypoint was not installed"
    # Invoke the jarvis console script with a timeout to avoid hangs
    proc = subprocess.run(
        ["jarvis", "--help"], capture_output=True, text=True, timeout=60
    )
    # The command should exit successfully
    assert proc.returncode == 0
    # Help output should mention the CLI description and list some commands
    assert "JARVIS command-line interface" in proc.stdout
    assert "ingest" in proc.stdout
    assert "db-init" in proc.stdout