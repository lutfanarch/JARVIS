"""Tests for the indicator computation engine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from informer.features.indicators import compute_indicators


def _generate_synthetic_bars(count: int, interval_minutes: int = 15):
    """Generate a synthetic intraday bar series for testing.

    Bars have deterministic open/high/low/close/volume values that
    increase over time to produce non-trivial indicator values.
    """
    bars = []
    base = datetime(2025, 1, 1, 9, 30, tzinfo=timezone.utc)
    for i in range(count):
        ts = base + timedelta(minutes=interval_minutes * i)
        close = float(i + 1)
        bar = {
            "ts": ts,
            "open": close - 0.5,
            "high": close + 0.5,
            "low": close - 1.0,
            "close": close,
            "volume": 100 + i,
        }
        bars.append(bar)
    return bars


def test_indicator_causality_no_lookahead() -> None:
    bars = _generate_synthetic_bars(60, interval_minutes=15)
    full = compute_indicators(bars, "15m")
    # Test indices where indicators have warmed up
    test_indices = [10, 25, 50]
    for idx in test_indices:
        truncated = compute_indicators(bars[: idx + 1], "15m")
        # Compare each indicator value at index idx
        full_row = full[idx]
        truncated_row = truncated[-1]
        for key in ["ema20", "ema50", "ema200", "rsi14", "atr14", "vwap"]:
            # Both None or approximately equal
            if full_row[key] is None or truncated_row[key] is None:
                assert full_row[key] == truncated_row[key]
            else:
                assert truncated_row[key] == pytest.approx(full_row[key], rel=1e-6)


def test_vwap_resets_each_ny_day() -> None:
    # Two bars on different NY dates
    # Bar1: Day1; Bar2: Day2; timeframe intraday (15m)
    bar1 = {
        "ts": datetime(2025, 1, 2, 14, 0, tzinfo=timezone.utc),
        "open": 1.0,
        "high": 2.0,
        "low": 0.5,
        "close": 1.5,
        "volume": 100,
    }
    bar2 = {
        # Next day in New York timezone (~23 hours later)
        "ts": datetime(2025, 1, 3, 14, 0, tzinfo=timezone.utc),
        "open": 2.0,
        "high": 3.0,
        "low": 1.5,
        "close": 2.5,
        "volume": 100,
    }
    bars = [bar1, bar2]
    results = compute_indicators(bars, "15m")
    # Typical prices
    tp1 = (bar1["high"] + bar1["low"] + bar1["close"]) / 3.0
    tp2 = (bar2["high"] + bar2["low"] + bar2["close"]) / 3.0
    # VWAP for first bar of day2 should equal its typical price since it's first in its NY day
    assert results[1]["vwap"] == pytest.approx(tp2)