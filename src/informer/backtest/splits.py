"""Utilities for trading day iteration and bar slicing.

This module provides helper functions for working with bar data in
different timeframes.  Functions here are pure and deterministic;
they take lists of bar dictionaries (or similar objects exposing
``ts``) and return subsets or aggregated views without modifying
the input.  Timezone conversions are handled via ``zoneinfo`` so
that decision times anchored in America/New_York can be translated
into UTC for database queries.

Bar objects are expected to be dictionaries with at least the
following keys:

* ``ts`` – timezone‑aware ``datetime`` object representing the bar's
  start time in UTC.
* ``open`` – float
* ``high`` – float
* ``low`` – float
* ``close`` – float
* ``volume`` – float

"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import List, Dict, Any, Iterable, Iterator
from zoneinfo import ZoneInfo

import pandas as pd


def trading_days(start_date: date, end_date: date) -> List[date]:
    """Return a list of trading dates (weekdays) between start and end inclusive.

    This helper does not account for market holidays; callers should
    supply a date range that excludes holiday periods.  Only weekdays
    (Monday–Friday) are included in the returned list.

    Parameters
    ----------
    start_date : date
        The first date to include.
    end_date : date
        The last date to include.

    Returns
    -------
    list of date
        Dates representing each trading day within the range.
    """
    days: List[date] = []
    dt = start_date
    while dt <= end_date:
        if dt.weekday() < 5:  # Monday=0, Sunday=6
            days.append(dt)
        dt += timedelta(days=1)
    return days


def bars_up_to(bars: Iterable[Dict[str, Any]], cutoff: datetime) -> List[Dict[str, Any]]:
    """Return bars with timestamps up to and including the cutoff.

    Parameters
    ----------
    bars : iterable of dict
        Sequence of bar dictionaries sorted by ``ts`` ascending.
    cutoff : datetime
        Timestamp in UTC.  Bars with ``ts`` <= ``cutoff`` are returned.

    Returns
    -------
    list of dict
        Bars up to the cutoff timestamp.
    """
    return [b for b in bars if b.get("ts") and b["ts"] <= cutoff]


def bars_after(bars: Iterable[Dict[str, Any]], start: datetime) -> List[Dict[str, Any]]:
    """Return bars strictly after the start timestamp.

    Parameters
    ----------
    bars : iterable of dict
        Sequence of bar dictionaries sorted by ``ts`` ascending.
    start : datetime
        Timestamp in UTC.  Only bars with ``ts`` > ``start`` are returned.

    Returns
    -------
    list of dict
        Bars after the start timestamp.
    """
    return [b for b in bars if b.get("ts") and b["ts"] > start]


def group_bars_by_day(
    bars: Iterable[Dict[str, Any]], tz: str = "America/New_York"
) -> Dict[date, List[Dict[str, Any]]]:
    """Group bars into trading days keyed by their local date in a timezone.

    Bars are assumed to be sorted by ``ts`` ascending.  Each bar's
    timestamp is converted to the given timezone and the date part
    extracted.  Bars are collected in lists keyed by that date.

    Parameters
    ----------
    bars : iterable of dict
        Sequence of bar dictionaries with ``ts`` keys.
    tz : str
        Timezone name for localizing timestamps (e.g., "America/New_York").

    Returns
    -------
    dict mapping date to list of dict
        A mapping from local trading date to the bars for that date.
    """
    zone = ZoneInfo(tz)
    grouped: Dict[date, List[Dict[str, Any]]] = {}
    for b in bars:
        ts = b.get("ts")
        if not ts:
            continue
        local_date = ts.astimezone(zone).date()
        grouped.setdefault(local_date, []).append(b)
    return grouped


def aggregate_bars(
    bars: Iterable[Dict[str, Any]], freq_minutes: int
) -> List[Dict[str, Any]]:
    """Aggregate lower timeframe bars into a higher timeframe by minutes.

    For example, to build 60‑minute bars from 15‑minute bars, set
    ``freq_minutes=60`` on a sequence of 15‑minute bars.  Aggregation
    preserves the first open, last close, max high, min low and sums
    volume.  ``ts`` on the aggregated bar is the timestamp of the
    first constituent bar.  ``vwap`` is recomputed as the volume‑
    weighted average price using ``typical`` price.

    Parameters
    ----------
    bars : iterable of dict
        Sequence of bar dictionaries sorted by ``ts`` ascending.
    freq_minutes : int
        Desired frequency in minutes for the aggregated bars.

    Returns
    -------
    list of dict
        Aggregated bars sorted by ``ts`` ascending.
    """
    bars_list = [b for b in bars]
    if not bars_list:
        return []
    # Build a DataFrame for easier resampling; reuse indicator helper
    df = pd.DataFrame.from_records([
        {
            "ts": b["ts"],
            "open": b["open"],
            "high": b["high"],
            "low": b["low"],
            "close": b["close"],
            "volume": b["volume"],
        }
        for b in bars_list
    ])
    df = df.sort_values("ts")
    df.set_index("ts", inplace=True)
    # Resample using a fixed frequency anchored at the first timestamp
    rule = f"{freq_minutes}min"
    ohlc = df.resample(rule, origin="start").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    ).dropna()
    result: List[Dict[str, Any]] = []
    for ts, row in ohlc.iterrows():
        result.append(
            {
                "ts": ts,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            }
        )
    return result
