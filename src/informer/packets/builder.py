"""Builder for informer packets.

This module contains a helper function that assembles a complete
``InformerPacket`` for a given symbol as of a specified timestamp.
It reads bar and feature data from the database, runs quality checks,
incorporates corporate actions and renders missing charts as needed.

The resulting packet is deterministic: given the same input parameters
and underlying database state it will produce identical output.  The
builder does not attempt to compute indicators or patterns on the fly;
it relies solely on previously ingested bars and feature snapshots.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from zoneinfo import ZoneInfo
import sqlalchemy as sa

from ..ingestion.bars import bars_table
from ..features.storage import features_snapshot_table
from ..ingestion.corporate_actions import corporate_actions_table
from ..quality.checks import run_bar_quality_checks, DataQualityEvent
from ..charts.renderer import render_chart_for_symbol_timeframe
from ..providers.alpaca import PROVIDER_VERSION

from .models import (
    SCHEMA_VERSION_DEFAULT,
    QAEvent,
    QASummary,
    BarOut,
    TimeframePacket,
    InformerPacket,
)


def _normalize_dt(value: datetime) -> datetime:
    """Normalize a datetime value to timezone‑aware UTC without microseconds.

    If the input is naive, UTC is assumed.  Microseconds are stripped
    for consistency with stored bar timestamps and to avoid jitter in
    packet generation.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    # Convert to UTC and strip microseconds
    value = value.astimezone(timezone.utc).replace(microsecond=0)
    return value


def _row_to_bar_out(row: sa.engine.Row) -> BarOut:
    """Convert a SQLAlchemy row from the bars table into a ``BarOut``.

    Ensures the timestamp is timezone aware and in UTC.  Other fields
    are passed through directly.
    """
    # RowMapping implements mapping interface via ._mapping
    mapping = row._mapping if hasattr(row, "_mapping") else row
    ts_val = mapping.get("ts")  # type: ignore[arg-type]
    if isinstance(ts_val, str):
        ts = datetime.fromisoformat(ts_val)
    else:
        ts = ts_val
    if ts is not None and ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return BarOut(
        ts=ts,
        open=mapping.get("open"),  # type: ignore[arg-type]
        high=mapping.get("high"),  # type: ignore[arg-type]
        low=mapping.get("low"),  # type: ignore[arg-type]
        close=mapping.get("close"),  # type: ignore[arg-type]
        volume=mapping.get("volume"),  # type: ignore[arg-type]
        vwap=mapping.get("vwap"),  # type: ignore[arg-type]
        source=mapping.get("source"),  # type: ignore[arg-type]
    )


def build_informer_packet(
    engine: sa.engine.Engine,
    symbol: str,
    as_of: datetime,
    timeframes: Iterable[str],
    limit_bars: int,
    feature_version: str,
    chart_version: str,
    charts_dir: Path,
    run_id: str,
    schema_version: str = SCHEMA_VERSION_DEFAULT,
    render_missing_charts: bool = True,
) -> InformerPacket:
    """Assemble an informer packet for a given symbol.

    Parameters
    ----------
    engine : sqlalchemy.engine.Engine
        Database engine bound to the target database.
    symbol : str
        Stock symbol to build the packet for.
    as_of : datetime
        Exclusive upper bound timestamp for bars and features.  If naive,
        it is assumed to be in UTC.  Microseconds are discarded.
    timeframes : Iterable[str]
        List of canonical timeframes to include (e.g., ["15m","1h","1d"]).
    limit_bars : int
        Maximum number of bars to include per timeframe.  Bars are
        returned in ascending order after limiting by this count.
    feature_version : str
        Feature version tag used to query the latest feature snapshot.
    chart_version : str
        Chart version tag used to determine expected chart file locations.
    charts_dir : pathlib.Path
        Base directory where charts are stored or will be rendered.
    run_id : str
        Unique identifier for this packet generation run (used in QA events).
    schema_version : str, optional
        Schema version to embed in the packet.  Defaults to
        ``SCHEMA_VERSION_DEFAULT``.
    render_missing_charts : bool, optional
        If True, attempt to render a chart via the renderer when the
        expected PNG file is missing.  If False, missing charts will
        result in a ``MISSING_CHART`` reason without attempting
        rendering.

    Returns
    -------
    informer.packets.models.InformerPacket
        A fully populated informer packet ready to be serialised to JSON.
    """
    # Normalise as_of to timezone‑aware UTC and remove microseconds
    as_of_norm = _normalize_dt(as_of)
    # Prepare result mapping for timeframes
    tf_packets: Dict[str, TimeframePacket] = {}
    # Connect once for all queries
    with engine.connect() as conn:
        # Loop through each requested timeframe
        for tf in timeframes:
            tf_lower = tf.lower()
            # Query latest bars up to as_of
            # We fetch in descending order to limit by last N, then reverse
            stmt = (
                sa.select(bars_table).where(
                    bars_table.c.symbol == symbol,
                    bars_table.c.timeframe == tf_lower,
                    bars_table.c.ts < as_of_norm,
                )
                .order_by(bars_table.c.ts.desc())
                .limit(limit_bars)
            )
            rows = conn.execute(stmt).fetchall()
            # Reverse to ascending chronological order
            bars_asc: List[BarOut] = []
            for row in reversed(rows):
                bar_out = _row_to_bar_out(row)
                bars_asc.append(bar_out)
            # Prepare default values
            latest_bar: Optional[BarOut] = bars_asc[-1] if bars_asc else None
            # Run quality checks
            qa_passed = False
            qa_errors: List[QAEvent] = []
            qa_warns: List[QAEvent] = []
            not_ready_reasons: List[str] = []
            if not bars_asc:
                # No bars: record reason and create QA error event via run_bar_quality_checks
                # run_bar_quality_checks will generate a NO_DATA event
                passed, events = run_bar_quality_checks(
                    symbol=symbol,
                    timeframe=tf_lower,
                    bars=[],
                    start=as_of_norm,
                    end=as_of_norm,
                    run_id=run_id,
                )
                qa_passed = passed
                for ev in events:
                    if ev.severity == "ERROR":
                        qa_errors.append(
                            QAEvent(
                                ts=ev.ts,
                                severity=ev.severity,
                                code=ev.code,
                                message=ev.message,
                            )
                        )
                    elif ev.severity == "WARN":
                        qa_warns.append(
                            QAEvent(
                                ts=ev.ts,
                                severity=ev.severity,
                                code=ev.code,
                                message=ev.message,
                            )
                        )
                not_ready_reasons.append("NO_DATA")
                # No features or charts available when no bars
                latest_features: Dict[str, Optional[object]] = {}
                chart_path: Optional[str] = None
            else:
                # Determine start timestamp for QA
                first_ts = bars_asc[0].ts
                if first_ts.tzinfo is None:
                    first_ts = first_ts.replace(tzinfo=timezone.utc)
                # Run quality checks on the actual bars
                passed, events = run_bar_quality_checks(
                    symbol=symbol,
                    timeframe=tf_lower,
                    bars=[{
                        "ts": b.ts,
                        "open": b.open,
                        "high": b.high,
                        "low": b.low,
                        "close": b.close,
                        "volume": b.volume,
                    } for b in bars_asc],
                    start=first_ts,
                    end=as_of_norm,
                    run_id=run_id,
                )
                qa_passed = passed
                for ev in events:
                    if ev.severity == "ERROR":
                        qa_errors.append(
                            QAEvent(
                                ts=ev.ts,
                                severity=ev.severity,
                                code=ev.code,
                                message=ev.message,
                            )
                        )
                    elif ev.severity == "WARN":
                        qa_warns.append(
                            QAEvent(
                                ts=ev.ts,
                                severity=ev.severity,
                                code=ev.code,
                                message=ev.message,
                            )
                        )
                # Look up latest features snapshot for the latest bar timestamp
                latest_features: Dict[str, Optional[object]] = {}
                ts_latest = latest_bar.ts if latest_bar else None
                if ts_latest is not None:
                    # Ensure timezone aware
                    if ts_latest.tzinfo is None:
                        ts_latest = ts_latest.replace(tzinfo=timezone.utc)
                    feat_stmt = sa.select(
                        features_snapshot_table.c.indicators_json,
                        features_snapshot_table.c.patterns_json,
                    ).where(
                        features_snapshot_table.c.symbol == symbol,
                        features_snapshot_table.c.timeframe == tf_lower,
                        features_snapshot_table.c.ts == ts_latest,
                        features_snapshot_table.c.feature_version == feature_version,
                    )
                    feat_row = conn.execute(feat_stmt).fetchone()
                    if feat_row is not None:
                        ind_json = feat_row._mapping.get("indicators_json")  # type: ignore[arg-type]
                        patt_json = feat_row._mapping.get("patterns_json")  # type: ignore[arg-type]
                        # Merge indicators and patterns under 'patterns' key
                        latest_features = {
                            **(ind_json or {}),
                            "patterns": patt_json or {},
                        }
                    else:
                        # No features snapshot found
                        latest_features = {}
                        not_ready_reasons.append("MISSING_FEATURES")
                # Determine chart path
                # Build expected path regardless of case; timeframe key used as passed
                expected_path = charts_dir / chart_version / symbol / f"{tf_lower}.png"
                chart_path: Optional[str] = None
                if expected_path.exists():
                    chart_path = str(expected_path)
                else:
                    # Chart missing; attempt to render if allowed
                    if render_missing_charts:
                        # Render chart using full window start to as_of
                        # Use warmup start equal to first_ts to preserve indicator alignment
                        gen_path = render_chart_for_symbol_timeframe(
                            engine=engine,
                            symbol=symbol,
                            timeframe=tf_lower,
                            start=first_ts,
                            end=as_of_norm,
                            out_dir=charts_dir,
                            chart_version=chart_version,
                            limit_bars=min(limit_bars, 200),
                        )
                        if gen_path is not None and Path(gen_path).exists():
                            chart_path = str(gen_path)
                        else:
                            not_ready_reasons.append("MISSING_CHART")
                    else:
                        # Chart missing and rendering disabled
                        not_ready_reasons.append("MISSING_CHART")
            # If we have bars but no latest features, ensure reason recorded
            if bars_asc and not latest_features:
                if "MISSING_FEATURES" not in not_ready_reasons:
                    not_ready_reasons.append("MISSING_FEATURES")
            if bars_asc and chart_path is None:
                if "MISSING_CHART" not in not_ready_reasons:
                    not_ready_reasons.append("MISSING_CHART")
            # Build QA summary
            qa_summary = QASummary(
                passed=qa_passed,
                errors=qa_errors,
                warnings=qa_warns,
            )
            tf_packets[tf] = TimeframePacket(
                timeframe=tf_lower,
                bars=bars_asc,
                latest_bar=latest_bar,
                latest_features=latest_features,
                qa=qa_summary,
                chart_path=chart_path,
                not_ready_reasons=not_ready_reasons,
            )
        # End of timeframe loop
        # Query corporate actions within the event window
        # Determine NY date window: as_of_norm converted to NY time
        ny_date = as_of_norm.astimezone(ZoneInfo("America/New_York")).date()
        start_date = ny_date - timedelta(days=7)
        end_date = ny_date + timedelta(days=90)
        events_stmt = sa.select(corporate_actions_table).where(
            corporate_actions_table.c.symbol == symbol,
            corporate_actions_table.c.ex_date >= start_date,
            corporate_actions_table.c.ex_date <= end_date,
        ).order_by(corporate_actions_table.c.ex_date.asc())
        event_rows = conn.execute(events_stmt).fetchall()
        corporate_events: List[Dict[str, object]] = []
        for erow in event_rows:
            m = erow._mapping if hasattr(erow, "_mapping") else erow
            # Build dict with symbol, action_type, ex_date iso and payload_json
            ev = {
                "symbol": m.get("symbol"),
                "action_type": m.get("action_type"),
                "ex_date": (m.get("ex_date").isoformat() if isinstance(m.get("ex_date"), date) else m.get("ex_date")),
                "payload_json": m.get("payload_json"),
            }
            corporate_events.append(ev)
    # Determine packet status
    status = "OK"
    for tf in timeframes:
        packet = tf_packets.get(tf)
        if packet is None:
            status = "NOT_READY"
            break
        # Bars must exist
        if not packet.bars:
            status = "NOT_READY"
            break
        # QA must pass
        if not packet.qa.passed:
            status = "NOT_READY"
            break
        # Must have features
        if not packet.latest_features:
            status = "NOT_READY"
            break
        # Must have chart
        if not packet.chart_path:
            status = "NOT_READY"
            break
    # Compose final packet
    informer_packet = InformerPacket(
        schema_version=schema_version,
        generated_at=as_of_norm,
        run_id=run_id,
        symbol=symbol,
        provider_version=PROVIDER_VERSION,
        feature_version=feature_version,
        chart_version=chart_version,
        status=status,
        timeframes=tf_packets,
        events={
            "corporate_actions": corporate_events,
            # Placeholders for future events
            "earnings": [],
            "macro": [],
        },
    )
    return informer_packet