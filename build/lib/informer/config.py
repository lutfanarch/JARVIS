"""
Configuration constants for the JARVIS project.

This module centralises configuration values that are used across the
application.  New values should be added here deliberately.
"""

from typing import Final

# Branding for the project.  The underlying Python package remains
# ``informer`` but the public documentation and help refer to JARVIS.
PROJECT_NAME: Final[str] = "JARVIS"

# Version identifier for the halal universe.  Bump this when
# expanding or shrinking the allowed symbol set.  This version is
# deterministic and does not depend on runtime state.
UNIVERSE_VERSION: Final[str] = "universe_v2_2026-01-14"

# Canonical whitelist (universe) of allowed trading symbols.  Only
# these symbols may be ingested, processed and emitted by the CLI
# commands.  The list is intentionally kept deterministic and order
# preserving.  To change the allowed universe, modify this list and
# increment ``UNIVERSE_VERSION`` accordingly.
CANONICAL_WHITELIST: Final[list[str]] = [
    "AAPL",
    "MSFT",
    "NVDA",
    "GOOGL",
    "GOOG",
    "AVGO",
    "META",
    "TSLA",
    "LLY",
    "XOM",
    "CVX",
    "JNJ",
    "ABBV",
    "MRK",
    "ABT",
    "TMO",
    "ISRG",
    "PG",
    "PEP",
    "HD",
    "ORCL",
    "CSCO",
    "CRM",
    "AMD",
    "MU",
    "INTC",
    "KLAC",
    "QCOM",
    "LRCX",
    "AMAT",
    "LIN",
]

__all__ = ["PROJECT_NAME", "UNIVERSE_VERSION", "CANONICAL_WHITELIST"]
