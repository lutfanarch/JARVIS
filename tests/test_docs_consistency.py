"""Ensure documentation remains aligned with CLI changes and official references.

This lightweight regression test checks that the core documentation files:

- include the official Trade The Pool references in `docs/propfirms.md`;
- do not contain outdated CLI patterns (e.g. ``informer forwardtest`` or
  references to Bash scripts like ``scripts/daily_scan.sh``);
- and, optionally, that the forward‑test documentation mentions the
  ``jarvis forwardtest`` command after the rewrite.

The test operates solely on the file system; it does not execute any
subprocesses and should run quickly.
"""

from pathlib import Path


def test_propfirm_docs_contain_official_references() -> None:
    """Ensure the prop firm documentation includes required TTP URLs."""
    doc_path = Path("docs/propfirms.md")
    content = doc_path.read_text(encoding="utf-8")
    required_urls = [
        "https://tradethepool.com/program-terms/",
        "https://tradethepool.com/the-program/",
        "https://tradethepool.com/fundamental/mastering-funded-trading-evaluation/",
    ]
    for url in required_urls:
        assert url in content, f"Missing official reference {url} in {doc_path}"


def test_docs_do_not_contain_outdated_patterns() -> None:
    """Ensure that outdated CLI and script references have been removed."""
    docs_dir = Path("docs")
    # Patterns that should not appear anywhere in the docs
    forbidden_substrings = [
        "informer forwardtest",
        "scripts/daily_scan.sh",
        "./scripts/daily_scan.sh",
    ]
    for doc_file in docs_dir.rglob("*.md"):
        content = doc_file.read_text(encoding="utf-8")
        for pattern in forbidden_substrings:
            assert pattern not in content, f"Outdated pattern '{pattern}' found in {doc_file}"


def test_forward_test_doc_mentions_jarvis_forwardtest() -> None:
    """Check that the forward test documentation references the jarvis forwardtest CLI."""
    fwd_path = Path("docs/forward_test.md")
    content_lower = fwd_path.read_text(encoding="utf-8").lower()
    assert "jarvis forwardtest" in content_lower, (
        "Forward test docs should reference the jarvis forwardtest command"
    )