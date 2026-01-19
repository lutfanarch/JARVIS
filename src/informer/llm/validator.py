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

# Import proprietary profile helpers for early risk and cash adjustments
from ..props.profiles import get_profile
import os


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
    # If arbiter indicates the system is not ready, propagate the
    # NOT_READY action without attempting any further validation or sizing.
    if arbiter.action == "NOT_READY":
        # Do not attempt to validate symbol/entry/stop or compute shares; simply
        # propagate the reason codes.  The caller is responsible for
        # attaching any audit information.
        return FinalDecision(
            **decision_common,
            action="NOT_READY",
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
    # Determine number of shares based on the effective risk budget and cash
    # Load the active proprietary profile for early risk/cash capping
    profile_name = os.getenv("PROP_PROFILE")
    profile = get_profile(profile_name) if profile_name else None
    # Derive effective budgets
    effective_max_risk_usd = max_risk_usd
    effective_cash_usd = cash_usd
    if profile:
        # Cap max risk to the profile's risk budget (in USD)
        effective_max_risk_usd = min(max_risk_usd, profile.risk_budget_usd)
        # Determine the available cash: if not provided, use full account size
        if cash_usd is None:
            effective_cash_usd = profile.account_size_usd
        else:
            effective_cash_usd = min(cash_usd, profile.account_size_usd)
        # Update decision_common to reflect the effective budgets
        decision_common["max_risk_usd"] = effective_max_risk_usd
        decision_common["cash_usd"] = effective_cash_usd
    else:
        # Ensure decision_common uses the original inputs
        decision_common["max_risk_usd"] = max_risk_usd
        decision_common["cash_usd"] = cash_usd
    # Compute share sizing using the effective budgets
    shares = floor(effective_max_risk_usd / risk_per_share)
    # Cap by cash if available
    if effective_cash_usd is not None:
        cash_cap = floor(effective_cash_usd / entry)
        shares = min(shares, cash_cap)
    # Require at least one share
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
    # Compute risk in USD and initial R multiple
    risk_usd = shares * risk_per_share
    r_multiple: Optional[float] = None
    if targets:
        first_target = targets[0]
        if first_target is not None and risk_per_share > 0:
            r_multiple = (first_target - entry) / risk_per_share
    # Build the initial final decision representing a valid trade
    final = FinalDecision(
        **decision_common,
        action="TRADE",
        symbol=sym,
        entry=entry,
        stop=stop,
        targets=list(targets),  # copy to allow mutation
        shares=shares,
        risk_usd=risk_usd,
        r_multiple=r_multiple,
        confidence=confidence,
        reason_codes=arbiter.reason_codes or [],
        audit={},
        prop=None,
    )
    # ------------------------------------------------------------------
    # Proprietary trading firm gates (deterministic)
    #
    # When a PROP_PROFILE is active and a trade is proposed, enforce
    # additional risk and validity gates defined by the profile.  If a
    # violation occurs, convert the trade into a NO_TRADE decision
    # carrying a PROP_RULE_VIOLATION reason code.  Otherwise, adjust
    # targets to respect the profit cap and attach a prop block to
    # the final decision.
    try:
        # Use the profile computed earlier; do not re-import or re-fetch.
        # Only apply gates when a known profile is requested and the
        # decision currently proposes a trade
        if profile and final.action == "TRADE":
            # Record the LLM‑proposed targets prior to any profile overrides.
            llm_targets_original: List[float] = list(final.targets) if final.targets else []
            # Apply default take‑profit policy: when the profile defines a
            # default_take_profit_r, override the primary target to a
            # deterministic multiple of the risk per share.  Preserve
            # the original targets in the audit for traceability.
            if profile.default_take_profit_r is not None:
                try:
                    # Compute risk per share using the current entry and stop
                    rps = final.entry - final.stop  # positive by earlier check
                    tp = final.entry + profile.default_take_profit_r * rps
                    # Override targets and r_multiple deterministically
                    final.targets = [tp]
                    final.r_multiple = profile.default_take_profit_r
                except Exception:
                    # In case of any error computing the target, leave as is
                    pass
                # Attach the LLM targets to the audit for transparency
                try:
                    final.audit["llm_targets_original"] = llm_targets_original
                except Exception:
                    pass
            # Capture targets after any default TP override for potential
            # profit cap adjustment below.
            original_targets: List[float] = list(final.targets) if final.targets else []
            # Compute budgetary values
            risk_budget_usd = profile.risk_budget_usd
            profit_cap_usd = profile.profit_cap_usd
            min_profit_per_share = profile.min_profit_per_share_usd
            # Risk gate: veto if computed risk exceeds the per‑trade budget
            if final.risk_usd is not None and final.risk_usd > risk_budget_usd:
                # Append violation code if not already present
                new_reasons = list(final.reason_codes) if final.reason_codes else []
                if "PROP_RULE_VIOLATION" not in new_reasons:
                    new_reasons.append("PROP_RULE_VIOLATION")
                # Convert to NO_TRADE
                final.action = "NO_TRADE"
                final.symbol = None
                final.entry = None
                final.stop = None
                final.targets = []
                final.shares = None
                # Retain risk_usd for audit before clearing it
                over_risk = final.risk_usd
                final.risk_usd = None
                final.r_multiple = None
                final.confidence = None
                final.reason_codes = new_reasons
                # Record violation details deterministically
                try:
                    final.audit["prop_violation"] = {
                        "type": "risk_budget_exceeded",
                        "risk_usd": over_risk,
                        "risk_budget_usd": risk_budget_usd,
                    }
                except Exception:
                    pass
            else:
                # Minimum profit per share gate: veto if any target falls short
                too_small = False
                if final.targets and final.entry is not None:
                    for t in final.targets:
                        # Profit per share for this target
                        profit_ps = t - final.entry
                        if profit_ps < min_profit_per_share:
                            too_small = True
                            break
                if too_small:
                    new_reasons = list(final.reason_codes) if final.reason_codes else []
                    if "PROP_RULE_VIOLATION" not in new_reasons:
                        new_reasons.append("PROP_RULE_VIOLATION")
                    final.action = "NO_TRADE"
                    final.symbol = None
                    final.entry = None
                    final.stop = None
                    final.targets = []
                    final.shares = None
                    final.risk_usd = None
                    final.r_multiple = None
                    final.confidence = None
                    final.reason_codes = new_reasons
                    # Record violation
                    try:
                        final.audit["prop_violation"] = {
                            "type": "min_profit_per_share",
                            "min_profit_per_share_usd": min_profit_per_share,
                        }
                    except Exception:
                        pass
                else:
                    # Profit cap adjustment: ensure no target implies a profit
                    # exceeding the cap.  We adjust the first violating
                    # target by reducing it to the cap per share or drop it
                    # altogether if it would not exceed the previous target.
                    adjusted_targets = None
                    if final.targets and final.entry is not None and final.shares:
                        shares = final.shares
                        entry_price = final.entry
                        # Determine maximum profit across targets
                        max_profit = max((t - entry_price) * shares for t in final.targets)
                        if max_profit > profit_cap_usd:
                            cap_per_share = profit_cap_usd / shares
                            new_targets = list(final.targets)
                            for idx, tgt in enumerate(new_targets):
                                profit_ps = tgt - entry_price
                                if profit_ps * shares > profit_cap_usd:
                                    # Proposed new target value
                                    new_tgt = entry_price + cap_per_share
                                    # If the new target is not greater than the previous
                                    # target, drop the violating target entirely
                                    if idx > 0 and new_tgt <= new_targets[idx - 1]:
                                        new_targets = new_targets[:idx]
                                    else:
                                        new_targets[idx] = new_tgt
                                    adjusted_targets = new_targets
                                    break
                            if adjusted_targets is not None:
                                final.targets = adjusted_targets
                                # Record adjustment in audit
                                try:
                                    final.audit["prop_target_adjustment"] = {
                                        "original_targets": original_targets,
                                        "adjusted_targets": adjusted_targets,
                                    }
                                except Exception:
                                    pass
                    # Attach prop block to the final decision since it
                    # remains a valid trade
                    try:
                        final.prop = {
                            "prop_profile_name": profile.name,
                            "risk_budget_usd": risk_budget_usd,
                            "profit_cap_usd": profit_cap_usd,
                            "min_profit_per_share_usd": min_profit_per_share,
                            "min_trade_duration_seconds": profile.min_trade_duration_seconds,
                        }
                    except Exception:
                        pass
    except Exception:
        # Any unexpected error in gating should not raise; fail closed
        # deterministically by vetoing the trade.
        try:
            new_reasons = list(final.reason_codes) if final.reason_codes else []
            if "PROP_RULE_VIOLATION" not in new_reasons:
                new_reasons.append("PROP_RULE_VIOLATION")
            final.action = "NO_TRADE"
            final.symbol = None
            final.entry = None
            final.stop = None
            final.targets = []
            final.shares = None
            final.risk_usd = None
            final.r_multiple = None
            final.confidence = None
            final.reason_codes = new_reasons
        except Exception:
            pass
    # ------------------------------------------------------------------
    return final