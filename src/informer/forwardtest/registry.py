"""Persistence layer for forward‑testing runs.

The forward‑testing registry records each shadow mode run to a JSONL
file stored under ``artifacts/forward_test``.  Each line in the file
contains a JSON object with metadata about the run, including the run
identifier, New York trade date, decision status and selected symbol.

This module exposes functions to record new runs, load the registry
into memory, and append outcome data to existing entries.  The registry
is append‑only and designed to be robust against concurrent writes in
shadow mode.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import UNIVERSE_VERSION
from ..providers.alpaca import PROVIDER_VERSION


def _registry_path() -> Path:
    """Return the path to the forward test registry JSONL file.

    The registry is stored at ``artifacts/forward_test/forward_test_runs.jsonl``.
    Parent directories are created if they do not exist.
    """
    p = Path("artifacts") / "forward_test" / "forward_test_runs.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_registry() -> List[Dict[str, Any]]:
    """Load all recorded forward test runs from the registry.

    Returns:
        A list of dictionaries representing recorded runs.  If the file
        does not exist or cannot be parsed, an empty list is returned.
    """
    path = _registry_path()
    if not path.exists():
        return []
    entries: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    entries.append(data)
                except Exception:
                    # Skip malformed lines
                    continue
    except Exception:
        # On any unexpected error, return empty list
        return []
    return entries


def _write_record(entry: Dict[str, Any]) -> None:
    """Append a single entry to the registry file.

    Args:
        entry: The run record to append.
    """
    path = _registry_path()
    # Open in append mode with line buffering; create if absent
    with path.open("a", encoding="utf-8") as f:
        json.dump(entry, f, ensure_ascii=False, sort_keys=True)
        f.write("\n")


def record_run(
    *,
    run_id: str,
    ny_date: str,
    mode: str,
    symbols: List[str],
    decision_status: str,
    selected_symbol: Optional[str],
    rationale_summary: Optional[str],
    schema_version: Optional[str],
    config_hash: str,
    artifact_dir: str,
    lock_key: str,
    provider_version: str = PROVIDER_VERSION,
    universe_version: str = UNIVERSE_VERSION,
) -> Dict[str, Any]:
    """Record a forward test run to the registry.

    This function builds a record dictionary from the supplied fields,
    sets the ``created_at_utc`` timestamp and appends the entry to the
    registry JSONL file.  The caller is responsible for ensuring that
    the entry does not violate any idempotency rules (e.g., only a
    single TRADE per date).  The entry is also returned to the caller.

    Args:
        run_id: The unique run identifier.
        ny_date: The New York trading date (YYYY-MM-DD).
        mode: The run mode, e.g. "shadow".
        symbols: List of symbols considered in the run.
        decision_status: The final decision status (TRADE/NO_TRADE/NOT_READY).
        selected_symbol: The symbol selected for trade, if any.
        rationale_summary: A free-text summary of decision rationale.
        schema_version: The decision or packet schema version.
        config_hash: A deterministic hash of the run configuration.
        artifact_dir: Relative path to the artifact directory for the run.
        lock_key: Identifier for the one-trade-per-day lock file.
        provider_version: Version string of the data provider.
        universe_version: Version string of the symbol universe.

    Returns:
        The record dictionary that was appended to the registry.
    """
    entry: Dict[str, Any] = {
        "run_id": run_id,
        "ny_date": ny_date,
        "created_at_utc": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "mode": mode,
        "symbols": symbols,
        "decision_status": decision_status,
        "selected_symbol": selected_symbol,
        "rationale_summary": rationale_summary,
        "schema_version": schema_version,
        "universe_version": universe_version,
        "provider_version": provider_version,
        "config_hash": config_hash,
        "artifact_dir": artifact_dir,
        "lock_key": lock_key,
    }
    _write_record(entry)
    return entry


def append_outcome(
    *,
    ny_date: str,
    symbol: str,
    entry: float,
    exit: float,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Append a user-provided outcome to the registry.

    When the user manually executes a forward-tested trade, they may
    record the realised entry and exit prices via this function.  The
    outcome is stored in a separate JSONL file named
    ``forward_test_outcomes.jsonl`` in the same directory as the
    registry.  Outcomes are append-only and keyed by ``ny_date`` and
    ``symbol``.

    Args:
        ny_date: The trade date in America/New_York (YYYY-MM-DD).
        symbol: The traded symbol.
        entry: Realised entry price.
        exit: Realised exit price.
        notes: Optional free-text notes.

    Returns:
        The outcome record that was appended.
    """
    path = Path("artifacts") / "forward_test" / "forward_test_outcomes.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ny_date": ny_date,
        "symbol": symbol,
        "entry": entry,
        "exit": exit,
        "notes": notes,
        "recorded_at_utc": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
    }
    with path.open("a", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, sort_keys=True)
        f.write("\n")
    return record