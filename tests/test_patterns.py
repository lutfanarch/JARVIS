"""Tests for candlestick pattern computation.

These tests verify that the pattern engine behaves correctly when the
TA‑Lib dependency is missing or when a dummy TA‑Lib implementation is
provided.  The pattern outputs should align to the input bars and
produce deterministic integer signals.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from types import ModuleType
from typing import List

import numpy as np
import pytest

from informer.features.patterns import compute_patterns, DEFAULT_PATTERNS


def _make_dummy_bars(n: int) -> List[dict]:
    """Create a simple list of bar dicts for testing.

    Each bar will have increasing timestamps spaced 15 minutes apart and
    deterministic OHLC values.  Volume is omitted because pattern
    computation does not use it.
    """
    base = datetime(2025, 1, 1, 9, 30, tzinfo=timezone.utc)
    bars = []
    for i in range(n):
        ts = base + timedelta(minutes=15 * i)
        bars.append(
            {
                "open": float(i) + 1.0,
                "high": float(i) + 2.0,
                "low": float(i) + 0.5,
                "close": float(i) + 1.5,
                "ts": ts,
            }
        )
    return bars


def test_patterns_fallback_no_talib(monkeypatch) -> None:
    """compute_patterns should return empty pattern dicts when TA‑Lib is missing."""
    # Ensure talib is not available in sys.modules
    import sys
    monkeypatch.setitem(sys.modules, "talib", None)
    bars = _make_dummy_bars(3)
    result = compute_patterns(bars, "15m")
    assert len(result) == len(bars)
    for entry in result:
        assert entry["patterns"] == {}


def test_patterns_with_dummy_talib(monkeypatch) -> None:
    """compute_patterns should produce signals using a dummy TA‑Lib implementation."""
    # Create dummy talib module with pattern functions returning deterministic values
    dummy = ModuleType("talib")

    def make_func(name: str):
        def func(open, high, low, close):  # type: ignore[no-untyped-def]
            # Produce a simple repeating pattern: 0, 100, -100
            values = []
            for idx in range(len(open)):
                if idx % 3 == 0:
                    val = 0
                elif idx % 3 == 1:
                    val = 100
                else:
                    val = -100
                values.append(val)
            return np.array(values, dtype=int)

        return func

    # Attach dummy functions for each default pattern
    for pat in DEFAULT_PATTERNS:
        setattr(dummy, pat, make_func(pat))
    # Monkeypatch sys.modules to inject dummy talib
    import sys
    monkeypatch.setitem(sys.modules, "talib", dummy)
    # Prepare bars and compute patterns
    bars = _make_dummy_bars(4)
    result = compute_patterns(bars, "15m")
    assert len(result) == len(bars)
    # Each entry should contain patterns for each default pattern
    for entry in result:
        patterns = entry["patterns"]
        assert set(patterns.keys()) == set(DEFAULT_PATTERNS)
        for val in patterns.values():
            assert isinstance(val, int)
            assert val in (-100, 0, 100)