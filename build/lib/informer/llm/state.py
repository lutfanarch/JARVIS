"""Simple trade lock persistence for one-trade-per-day enforcement.

This module defines a lightweight JSON-backed state file that tracks
the last trade date in the America/New_York timezone and the run
identifier that generated it.  The pipeline uses this lock to
enforce the ``one trade per day`` policy across successive runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class TradeLockState:
    """Represents the persisted state for trade lock enforcement.

    Attributes:
        last_trade_date_ny: The last date on which a trade was taken
            in the America/New_York timezone, formatted as YYYY-MM-DD.
        last_run_id: The run identifier that produced the trade on that
            date.
    """

    last_trade_date_ny: str
    last_run_id: str


def load_trade_lock(path: Path) -> Optional[TradeLockState]:
    """Load the trade lock state from a JSON file.

    Args:
        path: Path to the JSON file storing the trade lock state.

    Returns:
        A :class:`TradeLockState` if the file exists and can be parsed,
        otherwise ``None``.
    """
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return TradeLockState(
            last_trade_date_ny=data.get("last_trade_date_ny", ""),
            last_run_id=data.get("last_run_id", ""),
        )
    except FileNotFoundError:
        return None
    except Exception:
        # On any parsing error, treat as no lock
        return None


def save_trade_lock(path: Path, state: TradeLockState) -> None:
    """Persist the trade lock state to a JSON file.

    Parent directories are created if necessary.  The file is
    overwritten atomically.

    Args:
        path: Destination path for the JSON file.
        state: The state to persist.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "last_trade_date_ny": state.last_trade_date_ny,
                "last_run_id": state.last_run_id,
            },
            f,
            indent=2,
            sort_keys=True,
        )
    # Atomically replace existing file
    tmp_path.replace(path)