"""Features computation and persistence for Informer.

This package contains functions to compute technical indicators on OHLCV
bar data, as well as utilities for storing snapshots of these
features in the database.  Indicators are computed in a causal
fashion, meaning only past data influences the current value.
"""

from .indicators import compute_indicators
from .patterns import compute_patterns, DEFAULT_PATTERNS
from .regimes import compute_regimes
from .storage import features_snapshot_table, upsert_features_snapshot

__all__ = [
    "compute_indicators",
    "compute_patterns",
    "DEFAULT_PATTERNS",
    "compute_regimes",
    "features_snapshot_table",
    "upsert_features_snapshot",
]