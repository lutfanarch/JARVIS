"""Pydantic models for the Informer health check.

These models define the structured output for health checks.  Each
individual check produces a :class:`CheckResult` describing its
severity, a human‑readable message and any optional details.  The
aggregate :class:`HealthReport` summarises all checks along with
version metadata and a sanitised environment snapshot.
"""

from __future__ import annotations

from datetime import datetime, date
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# Severity levels used by health checks.  Errors indicate blocking
# issues that should prevent the system from running.  Warnings
# highlight potential misconfigurations or missing optional
# components.  Info entries are purely informational.
CheckSeverity = Literal["INFO", "WARN", "ERROR"]


class CheckResult(BaseModel):
    """Result of a single health check.

    Attributes:
        name: A short identifier for the check.
        severity: The outcome severity (INFO, WARN or ERROR).
        message: A human‑readable description of the check result.
        details: Optional structured details for additional context.
    """

    name: str
    severity: CheckSeverity
    message: str
    details: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        json_encoders = {
            datetime: lambda dt: dt.isoformat(),
            date: lambda d: d.isoformat(),
        }


class HealthReport(BaseModel):
    """Aggregated health report summarising multiple checks.

    The report contains a unique run identifier, a generation
    timestamp, an overall status, a list of check results sorted by
    name, a dictionary of version metadata and a sanitised snapshot
    of the runtime environment.
    """

    run_id: str
    generated_at: datetime
    status: Literal["OK", "NOT_READY"]
    checks: List[CheckResult]
    versions: Dict[str, str]
    environment: Dict[str, Any]

    class Config:
        json_encoders = {
            datetime: lambda dt: dt.isoformat(),
            date: lambda d: d.isoformat(),
        }