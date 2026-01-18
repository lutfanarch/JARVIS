"""Regime detection logic for Informer.

This module defines deterministic, causal regime labeling functions for
trend and volatility.  It operates on sequences of bars and their
precomputed indicators to derive regime labels aligned with each bar
timestamp.  The trend regime is based on the relative ordering of
exponential moving averages, while the volatility regime relies on
rolling quantiles of ATR percentage.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, List, Dict, Any

import pandas as pd  # type: ignore

from collections.abc import Mapping


def _get_ts(b: Any) -> datetime | None:
    """Extract and normalize timestamp from bar or dict.

    Returns a timezone‑aware UTC datetime or None.
    """
    # Support SQLAlchemy Row via _mapping
    if hasattr(b, "_mapping"):
        mapping = b._mapping  # type: ignore[attr-defined]
        t = mapping.get("ts")
    elif isinstance(b, Mapping):
        t = b.get("ts")
    else:
        t = getattr(b, "ts", None)
    if t is None:
        return None
    if isinstance(t, str):
        s = t
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except Exception:
            return None
    else:
        dt = t  # type: ignore[assignment]
    if isinstance(dt, datetime) and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt  # type: ignore[return-value]


def _safe_get(bar: Any, key: str) -> Any:
    """Retrieve attribute or mapping value without error."""
    if hasattr(bar, "_mapping"):
        return bar._mapping.get(key)
    if isinstance(bar, Mapping):
        return bar.get(key)
    return getattr(bar, key, None)


def compute_regimes(
    bars: Iterable[Dict[str, Any] | Any],
    indicators: List[Dict[str, Any]],
    timeframe: str,
) -> List[Dict[str, Any]]:
    """Compute trend and volatility regimes for a series of bars.

    Parameters
    ----------
    bars : iterable of bars (dicts or objects)
        The original bar data.  Used primarily to access closing
        prices.  Should be the same sequence used to compute
        ``indicators``.
    indicators : list of dict
        Output from :func:`~informer.features.indicators.compute_indicators`.
        Must be aligned 1:1 with the sorted bars by timestamp.
    timeframe : str
        The timeframe of the bars.  Currently unused but kept for
        API symmetry.

    Returns
    -------
    list of dict
        Each element contains:
        ``ts``: tz‑aware UTC datetime, normalized
        ``trend_regime``: str in {"uptrend", "downtrend", "range", "unknown"}
        ``vol_regime``: str in {"low", "normal", "high", "unknown"}
    """
    # Normalize bars and indicators alignment by timestamp.  We will
    # sort bars by timestamp (using the same normalization used in
    # compute_indicators) and zip with indicators.  compute_indicators
    # returns a list sorted by ts ascending.
    # Build a list of bars sorted by normalized ts
    bar_pairs: List[tuple[datetime | None, Any]] = []
    for b in bars:
        bar_pairs.append((_get_ts(b), b))
    bar_pairs.sort(key=lambda p: (p[0] is None, p[0] or datetime.min.replace(tzinfo=timezone.utc)))
    sorted_bars = [p[1] for p in bar_pairs]
    # Align lengths: ensure indicators length matches sorted_bars
    # Use min length to avoid index errors
    n = min(len(sorted_bars), len(indicators))
    # Lists to collect ATR percentages and regimes
    atr_pct_vals: List[float | None] = []
    regimes: List[Dict[str, Any]] = []
    # Extract ATR percentage list for rolling quantiles
    for i in range(n):
        ind = indicators[i]
        bar = sorted_bars[i]
        ts = ind.get("ts")
        if ts is None:
            ts = _get_ts(bar)
        if isinstance(ts, datetime) and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        # Trend regime
        ema20 = ind.get("ema20")
        ema50 = ind.get("ema50")
        ema200 = ind.get("ema200")
        if ema20 is None or ema50 is None or ema200 is None:
            trend = "unknown"
        elif ema20 > ema50 > ema200:
            trend = "uptrend"
        elif ema20 < ema50 < ema200:
            trend = "downtrend"
        else:
            trend = "range"
        # Volatility regime: compute atr_pct later after rolling quantiles
        atr14 = ind.get("atr14")
        # Need closing price from bar
        close_val = _safe_get(bar, "close")
        if atr14 is not None and close_val:
            try:
                atr_pct = float(atr14) / float(close_val) if float(close_val) > 0 else None
            except Exception:
                atr_pct = None
        else:
            atr_pct = None
        atr_pct_vals.append(atr_pct)
        regimes.append({"ts": ts, "trend_regime": trend, "vol_regime": "unknown"})
    # Compute rolling quantiles for volatility regime using pandas
    s = pd.Series(atr_pct_vals, dtype="float64")
    rolling_q33 = s.rolling(window=100, min_periods=20).quantile(0.33)
    rolling_q66 = s.rolling(window=100, min_periods=20).quantile(0.66)
    # Assign volatility regimes
    for i in range(n):
        atr_pct = atr_pct_vals[i]
        q33 = rolling_q33.iloc[i]
        q66 = rolling_q66.iloc[i]
        if atr_pct is None or pd.isna(q33) or pd.isna(q66):
            vol = "unknown"
        elif atr_pct <= q33:
            vol = "low"
        elif atr_pct >= q66:
            vol = "high"
        else:
            vol = "normal"
        regimes[i]["vol_regime"] = vol
    return regimes