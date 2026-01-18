"""Validator and sizing logic for LLM trade decisions.

This module implements a deterministic risk gate that validates
arbitrated trade proposals and computes whole-share sizing.  It
ensures long-only trades, enforces a maximum risk per trade in
USD and optional cash constraint, and rejects trades when
conditions are not met.  The resulting :class:`FinalDecision`
includes calculated sizing, risk metrics and preserves the
arbiter's confidence and reason codes.  When no trade is taken,
the validator propagates the NO_TRADE action along with
appropriate reason codes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import floor
from typing import List, Optional

from zoneinfo import ZoneInfo

from .models import (
    ArbiterDecision,
    FinalDecision,
    DECISION_SCHEMA_VERSION_DEFAULT,
)


def _compute_trade_date_ny(as_of: datetime) -> str:
    """Return the trade date in America/New_York as YYYY-MM-DD.

    If ``as_of`` is naive it is assumed to be UTC.
    """
    if as_of.tzinfo is None:
        # assume UTC if no timezone provided
        as_of = as_of.replace(tzinfo=ZoneInfo("UTC"))
    ny_dt = as_of.astimezone(ZoneInfo("America/New_York"))
    return ny_dt.date().isoformat()


def validate_and_size(
    arbiter: ArbiterDecision,
    *,
    as_of: datetime,
    run_id: str,
    whitelist: List[str],
    max_risk_usd: float,
    cash_usd: Optional[float] = None,
) -> FinalDecision:
    """Validate an arbiter decision and compute trade sizing.

    This function enforces long-only constraints, whitelist
    membership, risk caps and cash availability.  It returns a
    :class:`FinalDecision` representing either a sized trade or a
    no-trade outcome with reasons.  The ``generated_at`` field is
    set to the current UTC time with microseconds zeroed for
    determinism, and the trade date is computed in the
    America/New_York timezone.

    Args:
        arbiter: The decision from the arbiter stage.
        as_of: The reference timestamp for the decision (UTC).
        run_id: The run identifier for this decision run.
        whitelist: The list of allowed symbols.
        max_risk_usd: Maximum risk per trade in USD.
        cash_usd: Optional cash balance to further cap share count.

    Returns:
        FinalDecision: The validated and sized decision artifact.
    """
    # Normalize timestamps
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=ZoneInfo("UTC"))
    as_of = as_of.astimezone(ZoneInfo("UTC"))
    # Compute trade date in New York timezone
    trade_date_ny = _compute_trade_date_ny(as_of)
    # Initialise base fields
    generated_at = datetime.now(ZoneInfo("UTC")).replace(microsecond=0)
    decision_common = {
        "decision_schema_version": DECISION_SCHEMA_VERSION_DEFAULT,
        "run_id": run_id,
        "generated_at": generated_at,
        "as_of": as_of.replace(microsecond=0),
        "whitelist": whitelist,
        "max_risk_usd": max_risk_usd,
        "cash_usd": cash_usd,
        "trade_date_ny": trade_date_ny,
    }
    # If arbiter says no trade, propagate reasons
    if arbiter.action == "NO_TRADE":
        return FinalDecision(
            **decision_common,
            action="NO_TRADE",
            symbol=None,
            entry=None,
            stop=None,
            targets=[],
            shares=None,
            risk_usd=None,
            r_multiple=None,
            confidence=None,
            reason_codes=arbiter.reason_codes or [],
            audit={},
        )
    # Arbiter proposed a trade. Validate symbol is allowed.
    sym = arbiter.symbol
    entry = arbiter.entry
    stop = arbiter.stop
    targets = arbiter.targets or []
    confidence = arbiter.confidence
    if not sym or sym not in whitelist:
        return FinalDecision(
            **decision_common,
            action="NO_TRADE",
            symbol=None,
            entry=None,
            stop=None,
            targets=[],
            shares=None,
            risk_usd=None,
            r_multiple=None,
            confidence=None,
            reason_codes=["SYMBOL_NOT_ALLOWED"],
            audit={},
        )
    # Ensure entry and stop are present and entry > stop (long-only)
    if entry is None or stop is None or entry <= stop:
        return FinalDecision(
            **decision_common,
            action="NO_TRADE",
            symbol=None,
            entry=None,
            stop=None,
            targets=[],
            shares=None,
            risk_usd=None,
            r_multiple=None,
            confidence=None,
            reason_codes=["INVALID_PARAMETERS"],
            audit={},
        )
    # Compute risk per share
    risk_per_share = entry - stop
    if risk_per_share <= 0:
        return FinalDecision(
            **decision_common,
            action="NO_TRADE",
            symbol=None,
            entry=None,
            stop=None,
            targets=[],
            shares=None,
            risk_usd=None,
            r_multiple=None,
            confidence=None,
            reason_codes=["INVALID_PARAMETERS"],
            audit={},
        )
    # Determine number of shares based on max_risk_usd
    shares = floor(max_risk_usd / risk_per_share)
    # Cap by cash balance if provided
    if cash_usd is not None:
        cash_cap = floor(cash_usd / entry)
        shares = min(shares, cash_cap)
    if shares < 1:
        return FinalDecision(
            **decision_common,
            action="NO_TRADE",
            symbol=None,
            entry=None,
            stop=None,
            targets=[],
            shares=None,
            risk_usd=None,
            r_multiple=None,
            confidence=None,
            reason_codes=["RISK_TOO_HIGH_OR_CASH_TOO_LOW"],
            audit={},
        )
    # Compute risk in USD and r-multiple
    risk_usd = shares * risk_per_share
    # Determine first target and compute R multiple if available
    r_multiple: Optional[float] = None
    if targets:
        first_target = targets[0]
        if first_target is not None and risk_per_share > 0:
            r_multiple = (first_target - entry) / risk_per_share
    return FinalDecision(
        **decision_common,
        action="TRADE",
        symbol=sym,
        entry=entry,
        stop=stop,
        targets=targets,
        shares=shares,
        risk_usd=risk_usd,
        r_multiple=r_multiple,
        confidence=confidence,
        reason_codes=arbiter.reason_codes or [],
        audit={},
    )