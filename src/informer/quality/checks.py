"""Quality checks for bar data.

This module defines a set of deterministic checks that validate OHLCV
bar data for plausibility, continuity and freshness.  Each check
produces zero or more :class:`DataQualityEvent` instances.  A high
severity of ``ERROR`` indicates data corruption that should block
downstream processing, while ``WARN`` highlights potential issues that
may warrant investigation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Tuple, Union

from zoneinfo import ZoneInfo


@dataclass
class DataQualityEvent:
    """A structured record describing a quality issue detected in bar data."""

    run_id: str
    symbol: str
    timeframe: str
    ts: datetime
    severity: str  # "INFO", "WARN", "ERROR"
    code: str
    message: str


def _parse_bar(bar: Union[dict, object]) -> dict:
    """Normalize a bar record to a dict with expected keys.

    Bars retrieved from SQLAlchemy may be ``RowMapping`` objects or dicts.
    This helper extracts the relevant fields and ensures the timestamp
    is timezone aware (assumes UTC if naive).
    """
    if isinstance(bar, dict):
        record = bar
    else:
        # RowMapping supports mapping interface via ._mapping
        try:
            record = dict(bar)
        except Exception:
            # Fallback: attempt attribute access
            record = {k: getattr(bar, k) for k in ["ts", "open", "high", "low", "close", "volume"]}
    ts = record.get("ts")
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    if ts is not None and ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    record["ts"] = ts
    return record


def run_bar_quality_checks(
    symbol: str,
    timeframe: str,
    bars: Iterable[Union[dict, object]],
    start: datetime,
    end: datetime,
    run_id: str,
) -> Tuple[bool, List[DataQualityEvent]]:
    """Evaluate quality of a sequence of bars.

    Args:
        symbol: The symbol for which bars are being checked.
        timeframe: The canonical timeframe (e.g., ``15m``).
        bars: An iterable of bar records, either dicts or SQLAlchemy rows.
        start: The inclusive start timestamp of the window.
        end: The exclusive end timestamp of the window.
        run_id: A unique identifier for this quality check run.

    Returns:
        A tuple ``(passed, events)`` where ``passed`` is ``True`` if no
        ``ERROR`` events were produced, and ``events`` is a list of
        :class:`DataQualityEvent` describing issues.
    """
    # Normalize bars into a list of dicts with timezone-aware timestamps
    bar_list: List[dict] = []
    for b in bars:
        rec = _parse_bar(b)
        if rec.get("ts") is not None:
            bar_list.append(rec)
    # Sort bars chronologically
    bar_list.sort(key=lambda x: x["ts"])
    events: List[DataQualityEvent] = []
    passed = True
    # If no bars found in the window, emit a NO_DATA error
    if not bar_list:
        events.append(
            DataQualityEvent(
                run_id=run_id,
                symbol=symbol,
                timeframe=timeframe,
                ts=end,
                severity="ERROR",
                code="NO_DATA",
                message=f"No data available between {start.isoformat()} and {end.isoformat()}",
            )
        )
        passed = False
        return passed, events
    # Determine delta for timeframe (used for gap and stale checks)
    tf_lower = timeframe.lower()
    if tf_lower.endswith("m"):
        try:
            minutes = int(tf_lower[:-1])
        except Exception:
            minutes = 1
        delta = timedelta(minutes=minutes)
        is_intraday = True
    elif tf_lower.endswith("h"):
        try:
            hours = int(tf_lower[:-1])
        except Exception:
            hours = 1
        delta = timedelta(hours=hours)
        is_intraday = True
    else:
        # Daily or unknown: use one day
        delta = timedelta(days=1)
        is_intraday = False
    # OHLC and volume checks
    for rec in bar_list:
        ts = rec["ts"]
        # Ensure ts is timezone aware
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        o = rec.get("open")
        h = rec.get("high")
        l = rec.get("low")
        c = rec.get("close")
        v = rec.get("volume")
        # Price positivity
        if any(x is None or x <= 0 for x in [o, h, l, c]):
            events.append(
                DataQualityEvent(
                    run_id=run_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    ts=ts,
                    severity="ERROR",
                    code="OHLC_INVALID",
                    message="OHLC values must be positive",
                )
            )
            passed = False
        else:
            # Hierarchy: high >= max(open, close, low) and low <= min(open, close, high)
            if h < max(o, c, l) or l > min(o, c, h) or h < l:
                events.append(
                    DataQualityEvent(
                        run_id=run_id,
                        symbol=symbol,
                        timeframe=timeframe,
                        ts=ts,
                        severity="ERROR",
                        code="OHLC_INVALID",
                        message="Inconsistent OHLC relationship",
                    )
                )
                passed = False
        # Volume check
        if v is None or v < 0:
            events.append(
                DataQualityEvent(
                    run_id=run_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    ts=ts,
                    severity="ERROR",
                    code="VOLUME_INVALID",
                    message="Volume must be non-negative",
                )
            )
            passed = False
    # Timestamp strictly increasing
    for i in range(1, len(bar_list)):
        prev_ts = bar_list[i - 1]["ts"]
        cur_ts = bar_list[i]["ts"]
        if cur_ts <= prev_ts:
            events.append(
                DataQualityEvent(
                    run_id=run_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    ts=cur_ts,
                    severity="ERROR",
                    code="TS_NOT_STRICTLY_INCREASING",
                    message="Timestamps must be strictly increasing",
                )
            )
            passed = False
    # Gap within session (intraday only)
    if is_intraday and len(bar_list) > 1:
        ny_tz = ZoneInfo("America/New_York")
        gap_threshold = delta * 3
        for i in range(1, len(bar_list)):
            prev = bar_list[i - 1]
            cur = bar_list[i]
            prev_local_date = prev["ts"].astimezone(ny_tz).date()
            cur_local_date = cur["ts"].astimezone(ny_tz).date()
            if prev_local_date == cur_local_date:
                diff = cur["ts"] - prev["ts"]
                if diff > gap_threshold:
                    events.append(
                        DataQualityEvent(
                            run_id=run_id,
                            symbol=symbol,
                            timeframe=timeframe,
                            ts=cur["ts"],
                            severity="WARN",
                            code="GAP_WITHIN_SESSION",
                            message=f"Gap of {diff} exceeds threshold {gap_threshold}",
                        )
                    )
    # Stale latest check
    latest_ts = bar_list[-1]["ts"]
    # Ensure timestamps are timezone aware
    if latest_ts.tzinfo is None:
        latest_ts = latest_ts.replace(tzinfo=timezone.utc)
    end_utc = end
    if end_utc.tzinfo is None:
        end_utc = end_utc.replace(tzinfo=timezone.utc)
    diff_latest = end_utc - latest_ts
    if is_intraday:
        stale_threshold = delta * 3
        if diff_latest > stale_threshold:
            events.append(
                DataQualityEvent(
                    run_id=run_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    ts=end_utc,
                    severity="WARN",
                    code="STALE_LATEST",
                    message=f"Latest bar is stale by {diff_latest}",
                )
            )
    else:
        # For daily: threshold of 4 days
        if diff_latest > timedelta(days=4):
            events.append(
                DataQualityEvent(
                    run_id=run_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    ts=end_utc,
                    severity="WARN",
                    code="STALE_LATEST",
                    message=f"Latest bar is stale by {diff_latest}",
                )
            )
    return passed, events