"""Unit tests for the data quality checks engine."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from informer.quality.checks import run_bar_quality_checks


def test_no_data_emits_error() -> None:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = datetime(2025, 1, 2, tzinfo=timezone.utc)
    passed, events = run_bar_quality_checks("AAPL", "15m", [], start, end, "run1")
    assert not passed
    assert len(events) == 1
    ev = events[0]
    assert ev.code == "NO_DATA"
    assert ev.severity == "ERROR"
    assert ev.ts == end


def test_ohlc_invalid_emits_error() -> None:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = datetime(2025, 1, 2, tzinfo=timezone.utc)
    # First bar has negative open; second bar has high less than open
    bars = [
        {
            "ts": datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc),
            "open": -1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.0,
            "volume": 100,
        },
        {
            "ts": datetime(2025, 1, 1, 10, 15, tzinfo=timezone.utc),
            "open": 2.0,
            "high": 1.5,
            "low": 1.0,
            "close": 1.2,
            "volume": 100,
        },
    ]
    passed, events = run_bar_quality_checks("AAPL", "15m", bars, start, end, "run1")
    # Should not pass due to two invalid bars
    assert not passed
    # Expect at least two error events
    error_events = [ev for ev in events if ev.severity == "ERROR"]
    assert len(error_events) >= 2
    codes = {ev.code for ev in error_events}
    assert "OHLC_INVALID" in codes


def test_gap_within_session_emits_warn() -> None:
    start = datetime(2025, 1, 2, tzinfo=timezone.utc)
    end = datetime(2025, 1, 3, tzinfo=timezone.utc)
    # Two bars on the same NY date with a large gap (>45 minutes for 15m timeframe)
    b1_ts = datetime(2025, 1, 2, 14, 0, tzinfo=timezone.utc)
    b2_ts = datetime(2025, 1, 2, 18, 0, tzinfo=timezone.utc)  # 4 hours later
    bars = [
        {
            "ts": b1_ts,
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 100,
        },
        {
            "ts": b2_ts,
            "open": 1.6,
            "high": 2.1,
            "low": 1.0,
            "close": 1.8,
            "volume": 100,
        },
    ]
    passed, events = run_bar_quality_checks("AAPL", "15m", bars, start, end, "run1")
    # No errors expected
    assert passed
    # At least one warning for gap
    warn_events = [ev for ev in events if ev.severity == "WARN"]
    assert any(ev.code == "GAP_WITHIN_SESSION" for ev in warn_events)


def test_stale_latest_emits_warn() -> None:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = datetime(2025, 1, 10, tzinfo=timezone.utc)
    # For daily timeframe, a latest bar 9 days before end should trigger stale warning
    bars = [
        {
            "ts": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 100,
        },
    ]
    passed, events = run_bar_quality_checks("AAPL", "1d", bars, start, end, "run1")
    assert passed
    warn_events = [ev for ev in events if ev.severity == "WARN"]
    assert any(ev.code == "STALE_LATEST" for ev in warn_events)