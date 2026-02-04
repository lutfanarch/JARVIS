"""Data quality engine for Informer.

This package provides tools for evaluating the integrity and freshness of
stored bar data.  It produces structured events that can be stored in
the database for auditability.
"""

from .checks import DataQualityEvent, run_bar_quality_checks
from .storage import insert_quality_events, data_quality_events_table

__all__ = [
    "DataQualityEvent",
    "run_bar_quality_checks",
    "insert_quality_events",
    "data_quality_events_table",
]