"""Tests for the regimes computation engine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

import pytest

from informer.features.indicators import compute_indicators
from informer.features.regimes import compute_regimes


def _generate_bars(n: int) -> List[dict]:
    """Generate a list of synthetic bars for testing.

    The OHLC values increase linearly to create clear EMA ordering and
    non-zero ATR values.  Volume is constant.
    """
    base_ts = datetime(2025, 1, 1, 9, 30, tzinfo=timezone.utc)
    bars = []
    for i in range(n):
        ts = base_ts + timedelta(minutes=15 * i)
        bars.append(
            {
                "symbol": "AAPL",
                "timeframe": "15m",
                "ts": ts,
                "open": float(i) + 1.0,
                "high": float(i) + 2.0,
                "low": float(i) + 0.5,
                "close": float(i) + 1.5,
                "volume": 100 + i,
                "vwap": None,
                "source": "test",
            }
        )
    return bars


def test_regime_causality() -> None:
    """Regime labels should be causal (no look-ahead)."""
    # Generate a sufficiently long series
    bars = _generate_bars(120)
    full_indicators = compute_indicators(bars, "15m")
    full_regimes = compute_regimes(bars, full_indicators, "15m")
    # Define indices to test
    indices = [30, 60, 110]
    for idx in indices:
        truncated_bars = bars[: idx + 1]
        truncated_indicators = compute_indicators(truncated_bars, "15m")
        truncated_regimes = compute_regimes(truncated_bars, truncated_indicators, "15m")
        # Compare regime labels at index
        assert full_regimes[idx]["trend_regime"] == truncated_regimes[-1]["trend_regime"]
        assert full_regimes[idx]["vol_regime"] == truncated_regimes[-1]["vol_regime"]