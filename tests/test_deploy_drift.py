"""Regression tests to prevent deploy drift and legacy references (local-only).

These tests ensure that the repository no longer contains any Docker-based
deployment artifacts and that documentation does not reference Docker,
legacy Bash scripts, or the old `informer` CLI.  The intent is to harden
the operator workflow around the Windows-friendly local CLI and avoid any
confusion stemming from removed deployment paths.
"""

from pathlib import Path


def test_no_docker_directory() -> None:
    """Verify that the Docker deploy directory has been removed."""
    assert not (Path("deploy") / "docker").exists(), "deploy/docker directory should not exist"


def test_no_docker_references_in_docs_and_readme() -> None:
    """Ensure that README and docs do not reference Docker or compose commands."""
    targets = [Path("README.md")] + list(Path("docs").rglob("*.md"))
    forbidden_substrings = [
        "docker",
        "docker-compose",
        "compose up",
        "deploy/docker",
    ]
    for file_path in targets:
        content_lower = file_path.read_text(encoding="utf-8").lower()
        for pattern in forbidden_substrings:
            assert pattern not in content_lower, f"Forbidden pattern '{pattern}' found in {file_path}"


def test_no_legacy_references_in_docs_and_readme() -> None:
    """Check that README and docs do not reference legacy scripts or CLI."""
    targets = [Path("README.md")] + list(Path("docs").rglob("*.md"))
    forbidden_substrings = [
        "scripts/daily_scan.sh",
        "/app/scripts/daily_scan.sh",
        "python -m informer",
        "informer "  # legacy CLI prefix (with a trailing space to avoid false positives)
    ]
    for file_path in targets:
        content = file_path.read_text(encoding="utf-8")
        for pattern in forbidden_substrings:
            assert pattern not in content, f"Legacy pattern '{pattern}' found in {file_path}"
