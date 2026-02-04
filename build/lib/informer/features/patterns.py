"""Candlestick pattern recognition for Informer.

This module provides functionality to compute deterministic candlestick
pattern flags using the TA‑Lib library when available.  The pattern
outputs are aligned one‑to‑one with the input OHLCV bars and
returned as a list of dictionaries keyed by timestamp.  If the
optional TA‑Lib dependency is not installed, the functions in this
module will gracefully return empty pattern dictionaries without
raising errors.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, List, Dict, Optional, Any

from collections.abc import Mapping

import numpy as np

# List of candlestick patterns to compute.  The order here is
# intentionally fixed to ensure deterministic ordering in output
# dictionaries.  Do not reorder without updating tests.
DEFAULT_PATTERNS: List[str] = [
    "CDLDOJI",
    "CDLHAMMER",
    "CDLSHOOTINGSTAR",
    "CDLENGULFING",
    "CDLMORNINGSTAR",
    "CDLEVENINGSTAR",
]

# Internal helper to safely access values from different bar representations.
# Supports dict-like mappings, SQLAlchemy Row objects via ._mapping, or plain objects.
def _val(b: Any, key: str) -> Any:
    """Return the value for *key* from bar *b* without raising AttributeError.

    This function attempts to handle several bar representations:
    - Mapping (dict-like): returns b.get(key)
    - SQLAlchemy Row: uses the protected _mapping attribute
    - General object: uses getattr(b, key, None)
    """
    # Mapping (dict or similar)
    if isinstance(b, Mapping):
        return b.get(key)
    # SQLAlchemy Row exposes mapping via _mapping
    if hasattr(b, "_mapping"):
        mapping = getattr(b, "_mapping")
        if isinstance(mapping, Mapping):
            return mapping.get(key)
    # Fallback to attribute
    return getattr(b, key, None)


def compute_patterns(
    bars: Iterable[Dict[str, Any] | Any],
    timeframe: str,
    patterns: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Compute candlestick patterns for a sequence of bars.

    Parameters
    ----------
    bars: iterable of dict-like objects or SQLAlchemy rows
        Each element must contain keys or attributes: ``open``, ``high``,
        ``low``, ``close``, ``ts``.
    timeframe: str
        The canonical timeframe of the bars.  Unused here but kept
        for API symmetry with indicator computation.
    patterns: optional list of str
        The names of TA‑Lib candlestick pattern functions to compute.
        If omitted, :data:`DEFAULT_PATTERNS` will be used.

    Returns
    -------
    list of dict
        Each element corresponds to a bar and contains:
        ``ts``: the bar's timestamp (tz‑aware UTC)
        ``patterns``: mapping of pattern name to integer signal (100,
        -100 or 0).  If TA‑Lib is unavailable, the mapping will be
        empty.

    Notes
    -----
    - Timestamps are normalized to timezone‑aware UTC datetimes.
    - The caller should handle merging pattern results into other
      structures such as features_snapshot records.
    """
    # Prepare patterns list
    pattern_list = patterns if patterns is not None else DEFAULT_PATTERNS
    # Build list of (normalized_ts, original_bar) pairs
    pairs: List[tuple[Optional[datetime], Any]] = []
    for b in bars:
        raw_ts = _val(b, "ts")
        norm_ts: Optional[datetime] = None
        if raw_ts is not None:
            if isinstance(raw_ts, datetime):
                norm_ts = raw_ts
                if norm_ts.tzinfo is None:
                    norm_ts = norm_ts.replace(tzinfo=timezone.utc)
            elif isinstance(raw_ts, str):
                s = raw_ts
                # Treat trailing Z as UTC indicator
                if s.endswith("Z"):
                    s = s[:-1] + "+00:00"
                try:
                    norm_ts = datetime.fromisoformat(s)
                except Exception:
                    norm_ts = None
                if norm_ts is not None and norm_ts.tzinfo is None:
                    norm_ts = norm_ts.replace(tzinfo=timezone.utc)
        pairs.append((norm_ts, b))
    # Sort by normalized timestamp, treating None as minimal datetime
    def _sort_key(item: tuple[Optional[datetime], Any]) -> datetime:
        ts_val = item[0]
        if ts_val is None:
            # Use far past with UTC tzinfo
            return datetime.min.replace(tzinfo=timezone.utc)
        return ts_val

    pairs.sort(key=_sort_key)
    # Extract arrays from sorted bars and timestamps
    opens: List[float] = []
    highs: List[float] = []
    lows: List[float] = []
    closes: List[float] = []
    ts_list: List[Optional[datetime]] = []
    for ts_norm, b in pairs:
        # Append normalized ts
        ts_list.append(ts_norm)
        # Use safe accessor for values
        o = _val(b, "open")
        h = _val(b, "high")
        l = _val(b, "low")
        c = _val(b, "close")
        opens.append(float(o) if o is not None else np.nan)
        highs.append(float(h) if h is not None else np.nan)
        lows.append(float(l) if l is not None else np.nan)
        closes.append(float(c) if c is not None else np.nan)
    # Prepare output structure; patterns filled lazily below
    results: List[Dict[str, Any]] = [
        {"ts": ts if ts is not None else None, "patterns": {}} for ts in ts_list
    ]
    # Attempt to import TA‑Lib; if unavailable, return empty patterns
    try:
        import talib  # type: ignore[import]
    except Exception:
        return results
    # Convert to numpy arrays
    open_arr = np.array(opens, dtype="float64")
    high_arr = np.array(highs, dtype="float64")
    low_arr = np.array(lows, dtype="float64")
    close_arr = np.array(closes, dtype="float64")
    # Compute each pattern and populate results
    for pat_name in pattern_list:
        func = getattr(talib, pat_name, None)
        if func is None:
            # Skip unknown pattern names
            continue
        try:
            # TA‑Lib returns integer arrays (100, -100, 0)
            out = func(open_arr, high_arr, low_arr, close_arr)
        except Exception:
            # Skip pattern on error
            continue
        # Iterate results and assign int values
        for idx, val in enumerate(out):
            try:
                # Cast numpy scalar to Python int
                results[idx]["patterns"][pat_name] = int(val)
            except Exception:
                results[idx]["patterns"][pat_name] = 0
    return results