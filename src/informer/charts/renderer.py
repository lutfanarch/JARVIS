"""Deterministic chart renderer for Informer.

This module provides a function to generate standardized candlestick
charts from OHLCV data stored in the database.  Charts are rendered
using `mplfinance` with fixed styling and overlays (EMA, VWAP) per
timeframe.  The output PNG files are stored in a versioned directory
structure for reproducibility.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Any, List, Dict

from zoneinfo import ZoneInfo
import pandas as pd  # type: ignore

try:
    import mplfinance as mpf  # type: ignore
except Exception:
    mpf = None  # type: ignore

from sqlalchemy import select
from sqlalchemy.engine import Engine

from ..ingestion.bars import bars_table
from ..features.indicators import compute_indicators


# Default chart version used when none is specified
CHART_VERSION_DEFAULT: str = "v0.1"

# Exchange timezone used for plotting (America/New_York)
TIMEZONE_EXCHANGE: str = "America/New_York"


def _compute_fetch_start(tf_lower: str, start_dt: datetime) -> datetime:
    """Compute the start datetime for fetching bars including warmup buffer.

    For intraday timeframes, use 250 bars worth of duration; for daily,
    use 250 days.  This warmup ensures EMAs and other indicators are
    stable for the plotted period.
    """
    if tf_lower.endswith("m"):
        try:
            minutes = int(tf_lower[:-1])
        except Exception:
            minutes = 15
        buffer = timedelta(minutes=minutes * 250)
    elif tf_lower.endswith("h"):
        try:
            hours = int(tf_lower[:-1])
        except Exception:
            hours = 1
        buffer = timedelta(hours=hours * 250)
    else:
        buffer = timedelta(days=250)
    return start_dt - buffer


def render_chart_for_symbol_timeframe(
    engine: Engine,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    out_dir: Path,
    chart_version: str = CHART_VERSION_DEFAULT,
    limit_bars: int = 200,
) -> Optional[Path]:
    """Render a candlestick chart for a given symbol and timeframe.

    Parameters
    ----------
    engine : sqlalchemy.engine.Engine
        Database engine used to fetch bars.
    symbol : str
        The stock symbol to plot.
    timeframe : str
        The canonical timeframe (e.g., "15m", "1h", "1d").
    start : datetime
        Start datetime (inclusive) for the chart in UTC.  Must be tz-aware.
    end : datetime
        End datetime (exclusive) for the chart in UTC.  Must be tz-aware.
    out_dir : Path
        Directory where charts will be saved.  Subdirectories are
        created as ``<chart_version>/<symbol>/<timeframe>.png``.
    chart_version : str, optional
        Version string for the chart styling/format.  Default is
        ``CHART_VERSION_DEFAULT``.
    limit_bars : int, optional
        Maximum number of bars to plot.  Defaults to 200.  If the
        selected range has more bars, only the latest ``limit_bars``
        are shown (but indicators are computed using the full warmup).

    Returns
    -------
    Path or None
        The path to the written PNG file if data exists; otherwise
        ``None`` when no bars are available in the given range.
    """
    # Normalize timeframe
    tf_lower = timeframe.lower()
    # Ensure start and end are tz-aware UTC
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    # Compute fetch window with warmup buffer
    fetch_start = _compute_fetch_start(tf_lower, start)
    # Query bars with warmup window
    from sqlalchemy import and_  # local import to avoid top-level dependency
    with engine.connect() as conn:
        rows = conn.execute(
            select(bars_table).where(
                and_(
                    bars_table.c.symbol == symbol,
                    bars_table.c.timeframe == tf_lower,
                    bars_table.c.ts >= fetch_start,
                    bars_table.c.ts < end,
                )
            ).order_by(bars_table.c.ts)
        ).fetchall()
    if not rows:
        return None
    # Compute indicators over fetched rows
    indicators = compute_indicators(rows, tf_lower)
    # Build mapping of timestamp to bar row for quick lookup
    bar_map: Dict[datetime, Any] = {}
    for r in rows:
        ts = getattr(r, "ts") if hasattr(r, "ts") else r.get("ts")  # type: ignore[index]
        # Normalize ts to tz-aware UTC
        if isinstance(ts, datetime) and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        bar_map[ts] = r
    # Prepare DataFrame values and overlays for bars in [start, end)
    zone = ZoneInfo(TIMEZONE_EXCHANGE)
    values: List[Dict[str, Any]] = []
    index_list: List[datetime] = []
    # Series for overlays
    overlay_series: Dict[str, List[Optional[float]]] = {
        "ema20": [],
        "ema50": [],
        "ema200": [],
        "vwap": [],
    }
    # Iterate through indicators list (sorted ascending) and filter to chart window
    for ind in indicators:
        ts = ind.get("ts")
        if ts is None:
            continue
        # Ensure tz-aware
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < start or ts >= end:
            continue
        row = bar_map.get(ts)
        if not row:
            continue
        # Convert index to exchange-local naive datetime
        ts_plot = ts.astimezone(zone).replace(tzinfo=None)
        index_list.append(ts_plot)
        # Extract OHLCV values from row (SQLAlchemy Row or dict)
        def _safe_get(bar: Any, key: str) -> Any:
            if hasattr(bar, "_mapping"):
                return bar._mapping.get(key)
            if isinstance(bar, dict):
                return bar.get(key)
            return getattr(bar, key, None)

        o = _safe_get(row, "open")
        h = _safe_get(row, "high")
        l = _safe_get(row, "low")
        c = _safe_get(row, "close")
        v = _safe_get(row, "volume")
        values.append({
            "Open": float(o) if o is not None else None,
            "High": float(h) if h is not None else None,
            "Low": float(l) if l is not None else None,
            "Close": float(c) if c is not None else None,
            "Volume": float(v) if v is not None else None,
        })
        # Append overlays; None for missing values
        overlay_series["ema20"].append(ind.get("ema20"))
        overlay_series["ema50"].append(ind.get("ema50"))
        overlay_series["ema200"].append(ind.get("ema200"))
        overlay_series["vwap"].append(ind.get("vwap"))
    # Create DataFrame
    if not values:
        return None
    df = pd.DataFrame(values, index=index_list)
    # Limit bars to tail
    if limit_bars and len(df) > limit_bars:
        df = df.iloc[-limit_bars:]
        # Also truncate overlay_series accordingly
        for key in overlay_series:
            overlay_series[key] = overlay_series[key][-limit_bars:]
    # Build addplot list for overlays depending on timeframe
    addplots: List[Any] = []
    if mpf is not None:
        if tf_lower == "15m":
            if any(x is not None for x in overlay_series["ema20"]):
                addplots.append(mpf.make_addplot(pd.Series(overlay_series["ema20"], index=df.index), color="blue"))
            if any(x is not None for x in overlay_series["ema50"]):
                addplots.append(mpf.make_addplot(pd.Series(overlay_series["ema50"], index=df.index), color="orange"))
            if any(x is not None for x in overlay_series["vwap"]):
                addplots.append(mpf.make_addplot(pd.Series(overlay_series["vwap"], index=df.index), color="magenta"))
        elif tf_lower == "1h":
            if any(x is not None for x in overlay_series["ema20"]):
                addplots.append(mpf.make_addplot(pd.Series(overlay_series["ema20"], index=df.index), color="blue"))
            if any(x is not None for x in overlay_series["ema50"]):
                addplots.append(mpf.make_addplot(pd.Series(overlay_series["ema50"], index=df.index), color="orange"))
        else:  # daily
            if any(x is not None for x in overlay_series["ema20"]):
                addplots.append(mpf.make_addplot(pd.Series(overlay_series["ema20"], index=df.index), color="blue"))
            if any(x is not None for x in overlay_series["ema50"]):
                addplots.append(mpf.make_addplot(pd.Series(overlay_series["ema50"], index=df.index), color="orange"))
            if any(x is not None for x in overlay_series["ema200"]):
                addplots.append(mpf.make_addplot(pd.Series(overlay_series["ema200"], index=df.index), color="green"))
    # Determine output path
    out_path = Path(out_dir) / chart_version / symbol / f"{timeframe}.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if mpf is None:
        # If mplfinance is unavailable, we cannot render; return None
        return None
    # Set deterministic style and figure size
    style = mpf.make_mpf_style(base_mpf_style="yahoo")
    # Plot and save to file
    mpf.plot(
        df,
        type="candle",
        volume=True,
        style=style,
        addplot=addplots,
        savefig=dict(fname=str(out_path), dpi=150, bbox_inches="tight"),
        figratio=(8, 4),
    )
    return out_path