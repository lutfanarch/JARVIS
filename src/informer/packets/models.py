"""Pydantic models for informer packets.

These models describe the canonical JSON schema for informer packets
produced by the packet builder.  Each packet summarises data quality
information, recent bars across multiple timeframes, latest computed
features (indicators, patterns and regimes) and references to
rendered charts.  The schema is versioned to allow for future
evolutions without breaking downstream consumers.
"""

from __future__ import annotations

from datetime import datetime, date
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

# Default schema version for informer packets.  Increment this value
# when the structure of InformerPacket or its nested objects changes
# in a backwards-incompatible way.
SCHEMA_VERSION_DEFAULT: str = "v0.1"


class QAEvent(BaseModel):
    """Represents a single quality event within a timeframe.

    This model captures the timestamp of the issue and its severity,
    code and human‑readable message.  Only WARN and ERROR events are
    included in packet summaries; INFO events may be present in the
    database but are omitted here.
    """

    ts: datetime
    severity: str
    code: str
    message: str

    class Config:
        json_encoders = {
            datetime: lambda dt: dt.isoformat(),
            date: lambda d: d.isoformat(),
        }


class QASummary(BaseModel):
    """Summarises the results of running data quality checks.

    Attributes:
        passed: True if no ERROR events were detected.
        errors: List of error events detected by the quality engine.
        warnings: List of warning events detected by the quality engine.
    """

    passed: bool
    errors: List[QAEvent] = Field(default_factory=list)
    warnings: List[QAEvent] = Field(default_factory=list)

    class Config:
        json_encoders = {
            datetime: lambda dt: dt.isoformat(),
            date: lambda d: d.isoformat(),
        }


class BarOut(BaseModel):
    """Represents a bar in the packet output.

    This model mirrors the database schema but uses lower‑case field
    names for JSON friendliness.  The timestamp is always UTC and
    timezone aware.
    """

    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: Optional[float] = None
    source: Optional[str] = None

    class Config:
        json_encoders = {
            datetime: lambda dt: dt.isoformat(),
            date: lambda d: d.isoformat(),
        }


class TimeframePacket(BaseModel):
    """Packet information for a single timeframe.

    Attributes:
        timeframe: The canonical timeframe (e.g., "15m").
        bars: List of recent bars (ascending by ts).
        latest_bar: The most recent bar or None if no bars exist.
        latest_features: Latest computed features (indicators, regimes and patterns).
        qa: Summary of data quality checks for the selected window.
        chart_path: Path to the chart PNG file if available.
        not_ready_reasons: Codes indicating why this timeframe may not be ready.
    """

    timeframe: str
    bars: List[BarOut]
    latest_bar: Optional[BarOut]
    latest_features: Dict[str, Optional[object]]
    qa: QASummary
    chart_path: Optional[str] = None
    not_ready_reasons: List[str] = Field(default_factory=list)

    class Config:
        json_encoders = {
            datetime: lambda dt: dt.isoformat(),
            date: lambda d: d.isoformat(),
        }


class InformerPacket(BaseModel):
    """Top‑level informer packet for a symbol.

    The packet aggregates information across multiple timeframes and
    includes event data and versioning metadata.  Downstream systems
    should rely on the schema_version field to handle changes in
    structure over time.
    """

    schema_version: str
    generated_at: datetime
    run_id: str
    symbol: str
    provider_version: str
    feature_version: str
    chart_version: str
    status: str  # "OK" or "NOT_READY"
    timeframes: Dict[str, TimeframePacket]
    events: Dict[str, List[Dict[str, object]]]

    class Config:
        json_encoders = {
            datetime: lambda dt: dt.isoformat(),
            date: lambda d: d.isoformat(),
        }