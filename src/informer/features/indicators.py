"""Causal indicator computations for bar data.

This module implements a set of technical indicators commonly used in
trading systems.  Indicators are computed in a strictly causal manner:
only current and historical bars influence the value at a given
timestamp, never future data.  Functions return results aligned
one-to-one with the input bar sequence.
"""

from __future__ import annotations

from typing import Iterable, List, Dict, Any, Union
from datetime import datetime, timezone

import pandas as pd
from zoneinfo import ZoneInfo


def _to_dataframe(bars: Iterable[Union[dict, object]]) -> pd.DataFrame:
    """Convert a sequence of bar-like objects to a Pandas DataFrame.

    Bars may be dictionaries or objects with attribute access.  The
    resulting DataFrame has columns: ``ts``, ``open``, ``high``, ``low``,
    ``close``, ``volume``.  Timestamps are converted to timezone-aware
    datetimes (UTC assumed if naive).
    """
    records = []
    for b in bars:
        if b is None:
            continue
        if isinstance(b, dict):
            record = b
        else:
            # Try mapping behaviour first
            try:
                record = dict(b)
            except Exception:
                # fallback: attribute access
                record = {
                    "ts": getattr(b, "ts"),
                    "open": getattr(b, "open"),
                    "high": getattr(b, "high"),
                    "low": getattr(b, "low"),
                    "close": getattr(b, "close"),
                    "volume": getattr(b, "volume"),
                }
        ts = record.get("ts")
        if ts is None:
            continue
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        record["ts"] = ts
        # Only keep expected fields
        records.append(
            {
                "ts": record.get("ts"),
                "open": float(record.get("open", 0) or 0),
                "high": float(record.get("high", 0) or 0),
                "low": float(record.get("low", 0) or 0),
                "close": float(record.get("close", 0) or 0),
                "volume": float(record.get("volume", 0) or 0),
            }
        )
    df = pd.DataFrame.from_records(records)
    if not df.empty:
        df = df.sort_values("ts").reset_index(drop=True)
    return df


def compute_indicators(bars: Iterable[Union[dict, object]], timeframe: str) -> List[Dict[str, Any]]:
    """Compute causal technical indicators on a list of bars.

    Args:
        bars: An iterable of bar records.  Each bar should contain at
            least ``ts``, ``open``, ``high``, ``low``, ``close`` and
            ``volume`` keys or attributes.
        timeframe: Canonical timeframe string (e.g., ``15m``, ``1h``, ``1d``).

    Returns:
        A list of dictionaries with keys ``ts``, ``ema20``, ``ema50``,
        ``ema200``, ``rsi14``, ``atr14`` and ``vwap`` (or ``None`` for
        non-intraday timeframes).  The list is aligned one-to-one
        with the sorted input bars.
    """
    df = _to_dataframe(bars)
    # If no bars, return empty list
    if df.empty:
        return []
    # Compute typical price (high+low+close)/3 for VWAP
    df["typical"] = (df["high"] + df["low"] + df["close"]) / 3.0
    # EMA calculations on close prices
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()
    # RSI14 (Wilder)
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()
    # Avoid division by zero; where avg_loss is 0, define rs appropriately
    rs = avg_gain / avg_loss
    # Compute RSI
    rsi = 100 - 100 / (1 + rs)
    # Where avg_loss == 0 and avg_gain == 0, set RSI = 0
    rsi = rsi.mask((avg_loss == 0) & (avg_gain == 0), 0)
    # Where avg_loss == 0 and avg_gain > 0, set RSI = 100
    rsi = rsi.mask((avg_loss == 0) & (avg_gain > 0), 100)
    df["rsi14"] = rsi
    # ATR14 (Wilder)
    # True range calculation
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        (df["high"] - df["low"]),
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["atr14"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    # VWAP: only for intraday timeframes
    tf_lower = timeframe.lower()
    if tf_lower.endswith("m") or tf_lower.endswith("h"):
        # Convert ts to America/New_York date for reset
        ny_tz = ZoneInfo("America/New_York")
        local_dates = df["ts"].dt.tz_convert(ny_tz).dt.date
        # Accumulate typical_price * volume and volume within each local date
        df["tpv"] = df["typical"] * df["volume"]
        df["cum_tpv"] = df.groupby(local_dates)["tpv"].cumsum()
        df["cum_vol"] = df.groupby(local_dates)["volume"].cumsum()
        vwap = df["cum_tpv"] / df["cum_vol"]
        # Replace infinities or divisions by zero with NaN
        vwap = vwap.mask(df["cum_vol"] == 0)
        df["vwap"] = vwap
    else:
        df["vwap"] = None
    # Prepare output list with Python-native floats (cast NaN to None)
    result: List[Dict[str, Any]] = []
    for idx, row in df.iterrows():
        out: Dict[str, Any] = {"ts": row["ts"]}
        for col in ["ema20", "ema50", "ema200", "rsi14", "atr14", "vwap"]:
            val = row[col]
            # Convert pandas NaN or numpy nan to None
            if pd.isna(val):
                out[col] = None
            else:
                try:
                    out[col] = float(val)
                except Exception:
                    out[col] = None
        result.append(out)
    return result