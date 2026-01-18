"""Pydantic models for normalized provider outputs.

These classes define the canonical representation of market data bars and
corporate action events returned by data providers.  They ensure a stable,
LLM-friendly schema for downstream processing.
"""

from __future__ import annotations

from datetime import datetime, date
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class Bar(BaseModel):
    """Represents a single OHLCV bar with optional VWAP.

    Attributes:
        symbol: The stock symbol (e.g. "AAPL").
        timeframe: The bar interval (e.g. "15m", "1h", "1d").
        ts: The timestamp of the bar as an aware datetime.
        open: The opening price.
        high: The highest price.
        low: The lowest price.
        close: The closing price.
        volume: The traded volume.
        vwap: The volume weighted average price for the bar, if available.
        source: The name of the data provider.
    """

    symbol: str
    timeframe: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: Optional[float] = None
    source: str = Field(default="alpaca")

    class Config:
        json_encoders = {
            datetime: lambda dt: dt.isoformat(),
            date: lambda d: d.isoformat(),
        }


class CorporateAction(BaseModel):
    """Represents a corporate action announcement.

    Attributes:
        symbol: The stock symbol affected by the corporate action.
        action_type: A short string describing the type of action (e.g. "dividend", "split").
        ex_date: The ex-date of the action.
        payload_json: A dictionary containing the raw announcement payload.
        source: The name of the data provider.
    """

    symbol: str
    action_type: str
    ex_date: date
    payload_json: Dict[str, Any]
    source: str = Field(default="alpaca")

    class Config:
        json_encoders = {
            datetime: lambda dt: dt.isoformat(),
            date: lambda d: d.isoformat(),
        }