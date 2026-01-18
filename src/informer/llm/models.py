"""Pydantic models for the LLM analysis layer.

These models define the structured inputs and outputs for the Phase 2
analysis pipeline.  Stage A produces screener candidates, Stage B
evaluates candidates via analyst and critic roles, Stage C performs
arbitration and the final result is a ``FinalDecision`` describing
the selected trade (if any) and sizing information.  All datetime
fields are timezone‑aware and microsecond precision is removed for
determinism.
"""

from __future__ import annotations

from datetime import datetime, date
from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field


# Default schema version for decision outputs.  Increment when
# breaking changes are introduced to the structure of FinalDecision or
# related models.
DECISION_SCHEMA_VERSION_DEFAULT: str = "v0.1"


class ScreenerCandidate(BaseModel):
    """Represents a potential trade candidate identified by the screener."""

    symbol: str
    setup_hint: Optional[str] = None
    reason_codes: List[str] = Field(default_factory=list)


class ScreenerOutput(BaseModel):
    """Output of the screener stage.

    The screener may return one or more candidates or decide that no
    trade should be taken.  The ``action`` field determines whether
    there are candidates to evaluate.  Candidates must not exceed the
    maximum specified in the pipeline.
    """

    schema_version: str = DECISION_SCHEMA_VERSION_DEFAULT
    action: Literal["NO_TRADE", "CANDIDATES"]
    candidates: List[ScreenerCandidate] = Field(default_factory=list)
    notes: Optional[str] = None


class AnalystPlan(BaseModel):
    """Plan proposed by the analyst agent for a given candidate."""

    action: Literal["PROPOSE_TRADE", "REJECT"]
    entry: Optional[float] = None
    stop: Optional[float] = None
    targets: List[float] = Field(default_factory=list)
    confidence: Optional[float] = None  # 0..1
    reason_codes: List[str] = Field(default_factory=list)
    notes: Optional[str] = None


class CriticReview(BaseModel):
    """Review and verdict from the critic agent for a given candidate."""

    verdict: Literal["APPROVE", "REJECT"]
    issues: List[str] = Field(default_factory=list)
    reason_codes: List[str] = Field(default_factory=list)
    notes: Optional[str] = None


class CandidateEvaluation(BaseModel):
    """Combined evaluation of a candidate by analyst and critic."""

    symbol: str
    analyst: AnalystPlan
    critic: CriticReview


class ArbiterDecision(BaseModel):
    """Decision from the arbiter on which candidate to trade (if any)."""

    action: Literal["TRADE", "NO_TRADE"]
    symbol: Optional[str] = None
    entry: Optional[float] = None
    stop: Optional[float] = None
    targets: List[float] = Field(default_factory=list)
    confidence: Optional[float] = None
    reason_codes: List[str] = Field(default_factory=list)
    notes: Optional[str] = None


class FinalDecision(BaseModel):
    """Final decision artifact emitted by the pipeline.

    Includes the arbitrated trade parameters, share sizing, risk
    calculations and a full audit trail of intermediate stage
    outputs.  The trade date is always represented in the
    America/New_York timezone.  Use ``cash_usd`` to optionally cap
    share sizing by available cash.
    """

    decision_schema_version: str = DECISION_SCHEMA_VERSION_DEFAULT
    run_id: str
    generated_at: datetime
    as_of: datetime
    whitelist: List[str]
    max_risk_usd: float
    cash_usd: Optional[float] = None
    action: Literal["TRADE", "NO_TRADE"]
    trade_date_ny: str
    symbol: Optional[str] = None
    entry: Optional[float] = None
    stop: Optional[float] = None
    targets: List[float] = Field(default_factory=list)
    shares: Optional[int] = None
    risk_usd: Optional[float] = None
    r_multiple: Optional[float] = None
    confidence: Optional[float] = None
    reason_codes: List[str] = Field(default_factory=list)
    audit: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        json_encoders = {
            datetime: lambda dt: dt.isoformat(),
            date: lambda d: d.isoformat(),
        }