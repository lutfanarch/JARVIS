"""Informer packet models and builder.

This package defines Pydantic models that describe the canonical
structure of an informer packet as well as a builder function to
construct packets from data stored in the database.  Packets are
versioned and provide a deterministic summary of the current state of
market data, quality checks, computed features and charts for a
given symbol.
"""

from .models import (
    SCHEMA_VERSION_DEFAULT,
    QAEvent,
    QASummary,
    BarOut,
    TimeframePacket,
    InformerPacket,
)
from .builder import build_informer_packet

__all__ = [
    "SCHEMA_VERSION_DEFAULT",
    "QAEvent",
    "QASummary",
    "BarOut",
    "TimeframePacket",
    "InformerPacket",
    "build_informer_packet",
]