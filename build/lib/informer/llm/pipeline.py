"""Decision pipeline orchestration for Informer Phase 2.

This module coordinates the multi‑stage analysis performed by the
language model layer.  It reads informer packets from disk,
invokes the screener, analyst, critic and arbiter via an LLM
client, applies deterministic validation and sizing and enforces a
one‑trade‑per‑day policy via a lock file.  The result is a
``FinalDecision`` object containing the selected trade (if any) and
a complete audit trail of intermediate outputs.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from zoneinfo import ZoneInfo

from ..packets.models import InformerPacket
from ..llm.client import LLMClient, FakeLLMClient, parse_json_response
from ..llm.models import (
    DECISION_SCHEMA_VERSION_DEFAULT,
    ScreenerOutput,
    CandidateEvaluation,
    ArbiterDecision,
    FinalDecision,
    ScreenerCandidate,
    AnalystPlan,
    CriticReview,
)
from ..llm.validator import validate_and_size
from ..llm.state import load_trade_lock, save_trade_lock, TradeLockState


def load_packets(packets_dir: Path, symbols: List[str]) -> Dict[str, Optional[InformerPacket]]:
    """Load informer packets for the given symbols from a directory.

    The packet files are expected to be named ``<symbol>.json``.  If a
    file does not exist or cannot be parsed, the corresponding value
    in the returned dictionary will be ``None``.

    Args:
        packets_dir: Directory containing packet JSON files.
        symbols: List of symbol strings to load.

    Returns:
        A dictionary mapping each symbol to an :class:`InformerPacket` or
        ``None`` if the packet is missing or invalid.
    """
    result: Dict[str, Optional[InformerPacket]] = {}
    for sym in symbols:
        path = packets_dir / f"{sym}.json"
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            packet = InformerPacket.model_validate(data)
        except Exception:
            packet = None
        result[sym] = packet
    return result


def _select_timeframe_for_symbol(packet: InformerPacket) -> str:
    """Choose a preferred timeframe for screening and evaluation.

    Preference order is 15m, 1h, then 1d.  If none of these
    timeframes are present in the packet, the first available
    timeframe key is returned.

    Args:
        packet: The informer packet from which to select a timeframe.

    Returns:
        A timeframe string present in the packet.
    """
    preferred = ["15m", "1h", "1d"]
    for tf in preferred:
        if tf in packet.timeframes:
            return tf
    # Fallback to first key
    for tf in packet.timeframes.keys():
        return tf
    # Should not reach here due to schema guarantee
    return "15m"


def run_decision_pipeline(
    *,
    packets: Dict[str, Optional[InformerPacket]],
    as_of: datetime,
    run_id: str,
    whitelist: List[str],
    max_candidates: int,
    llm: LLMClient,
    max_risk_usd: float,
    cash_usd: Optional[float],
    trade_lock_path: Path,
) -> FinalDecision:
    """Execute the full decision pipeline.

    This function coordinates all stages of analysis: screener,
    analyst, critic, arbiter, validation/sizing and trade lock
    enforcement.  It returns a :class:`FinalDecision` summarising
    the chosen trade (if any) and an audit trail of intermediate
    outputs.  The pipeline never raises on invalid/missing data;
    instead it produces a NO_TRADE decision with explanatory
    reason codes.

    Args:
        packets: Mapping of symbol to informer packet or None.
        as_of: Reference timestamp for the decision (UTC or naive assumed
            UTC).  Microseconds are ignored.
        run_id: Unique identifier for this decision run.
        whitelist: Allowed symbols for trading.
        max_candidates: Maximum number of candidates to forward from
            the screener to evaluation (hard cap at 2 enforced by
            caller).
        llm: Language model client (e.g., :class:`FakeLLMClient`).
        max_risk_usd: Maximum USD risk per trade used by the validator.
        cash_usd: Optional cash balance cap for share sizing.
        trade_lock_path: Path to the trade lock file used for
            one‑trade‑per‑day enforcement.

    Returns:
        FinalDecision: The outcome of the pipeline including the audit
        trail.
    """
    # Normalize as_of to UTC with zero microseconds
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=ZoneInfo("UTC"))
    as_of = as_of.astimezone(ZoneInfo("UTC")).replace(microsecond=0)
    # Pre-gate: compile packet summaries for screener
    screener_inputs: List[Dict[str, object]] = []
    symbol_to_timeframe: Dict[str, str] = {}
    for sym, packet in packets.items():
        if not packet:
            continue
        if packet.status != "OK":
            continue
        # Choose timeframe
        tf = _select_timeframe_for_symbol(packet)
        symbol_to_timeframe[sym] = tf
        tf_packet = packet.timeframes.get(tf)
        if not tf_packet:
            continue
        latest_features = tf_packet.latest_features or {}
        trend = latest_features.get("trend_regime")
        vol = latest_features.get("vol_regime")
        qa_passed = tf_packet.qa.passed
        screener_inputs.append(
            {
                "symbol": sym,
                "trend_regime": trend,
                "vol_regime": vol,
                "qa_passed": qa_passed,
            }
        )
    # Call screener via LLM
    screener_payload = {
        "packets": screener_inputs,
        "max_candidates": max_candidates,
    }
    screener_response_text = llm.complete(
        purpose="screener",
        system="You are a deterministic screener.",
        user=json.dumps(screener_payload),
    )
    # Parse screener output
    try:
        screener_output = parse_json_response(screener_response_text, ScreenerOutput)
    except Exception:
        # Fail-safe: produce a no-trade outcome with reason
        screener_output = ScreenerOutput(
            schema_version=DECISION_SCHEMA_VERSION_DEFAULT,
            action="NO_TRADE",
            candidates=[],
            notes="SCREENER_PARSE_ERROR",
        )  # type: ignore[name-defined]
    # Prepare audit container
    audit: Dict[str, object] = {"screener_output": screener_output.model_dump()}
    # If no candidates, return no trade
    if screener_output.action == "NO_TRADE" or not screener_output.candidates:
        # Build arbiter decision for validation
        arbiter_decision = ArbiterDecision(
            action="NO_TRADE",
            symbol=None,
            entry=None,
            stop=None,
            targets=[],
            confidence=None,
            reason_codes=["NO_CANDIDATES"],
            notes=None,
        )
        # Validate and size (will propagate no trade)
        final_decision = validate_and_size(
            arbiter_decision,
            as_of=as_of,
            run_id=run_id,
            whitelist=whitelist,
            max_risk_usd=max_risk_usd,
            cash_usd=cash_usd,
        )
        final_decision.audit = audit
        # Trade lock check: also enforce one trade per day (no trade so nothing to save)
        return final_decision
    # Stage B: evaluate candidates
    evaluations: List[CandidateEvaluation] = []
    # Track whether any critic call failed.  If set, the pipeline will
    # immediately return a no‑trade decision with a CRITIC_UNAVAILABLE
    # reason code.
    critic_error_occurred = False
    for candidate in screener_output.candidates:
        sym = candidate.symbol
        packet = packets.get(sym)
        if not packet or packet.status != "OK":
            continue
        # Determine timeframe used earlier
        tf = symbol_to_timeframe.get(sym)
        if not tf:
            tf = _select_timeframe_for_symbol(packet)
            symbol_to_timeframe[sym] = tf
        tf_packet = packet.timeframes.get(tf)
        if not tf_packet:
            continue
        # Extract latest close from latest_bar
        latest_bar = tf_packet.latest_bar
        latest_close: Optional[float] = None
        if latest_bar:
            latest_close = latest_bar.close
        # Extract ATR14 from latest_features
        latest_features = tf_packet.latest_features or {}
        atr14 = latest_features.get("atr14")
        vol = latest_features.get("vol_regime")
        qa_passed = tf_packet.qa.passed
        # Analyst call
        analyst_payload = {
            "symbol": sym,
            "latest_close": latest_close,
            "atr14": atr14,
            "vol_regime": vol,
            "qa_passed": qa_passed,
        }
        analyst_text = llm.complete(
            purpose="analyst",
            system="You are a deterministic analyst.",
            user=json.dumps(analyst_payload),
        )
        try:
            analyst_plan = parse_json_response(analyst_text, AnalystPlan)  # type: ignore[name-defined]
        except Exception:
            analyst_plan = AnalystPlan(
                action="REJECT",  # type: ignore[name-defined]
                entry=None,
                stop=None,
                targets=[],
                confidence=None,
                reason_codes=["ANALYST_PARSE_ERROR"],
                notes=None,
            )
        # Critic call
        critic_payload = {
            "symbol": sym,
            "vol_regime": vol,
            "qa_passed": qa_passed,
        }
        try:
            critic_text = llm.complete(
                purpose="critic",
                system="You are a deterministic critic.",
                user=json.dumps(critic_payload),
            )
        except Exception:
            # Record critic failure and stop evaluating further candidates
            critic_error_occurred = True
            break
        try:
            critic_review = parse_json_response(critic_text, CriticReview)  # type: ignore[name-defined]
        except Exception:
            critic_review = CriticReview(
                verdict="REJECT",  # type: ignore[name-defined]
                issues=["CRITIC_PARSE_ERROR"],
                reason_codes=[],
                notes=None,
            )
        evaluations.append(
            CandidateEvaluation(
                symbol=sym,
                analyst=analyst_plan,
                critic=critic_review,
            )
        )
    audit["evaluations"] = [ev.model_dump() for ev in evaluations]
    # If any critic call failed, return immediately with a no‑trade decision
    # and a CRITIC_UNAVAILABLE reason code.  Skip the arbiter stage entirely
    # because evaluations are incomplete.  Audit still includes the
    # screener_output and any evaluations performed before the failure.
    if critic_error_occurred:
        arbiter_decision = ArbiterDecision(
            action="NO_TRADE",
            symbol=None,
            entry=None,
            stop=None,
            targets=[],
            confidence=None,
            reason_codes=["CRITIC_UNAVAILABLE"],
            notes=None,
        )
        # Note: do not include arbiter_decision in audit when critic failed
        final_decision = validate_and_size(
            arbiter_decision,
            as_of=as_of,
            run_id=run_id,
            whitelist=whitelist,
            max_risk_usd=max_risk_usd,
            cash_usd=cash_usd,
        )
        final_decision.audit = audit
        return final_decision

    # Stage C: arbiter
    arbiter_payload = {
        "evaluations": [
            {
                "symbol": ev.symbol,
                "analyst": ev.analyst.model_dump(),
                "critic": ev.critic.model_dump(),
            }
            for ev in evaluations
        ]
    }
    arbiter_text = llm.complete(
        purpose="arbiter",
        system="You are a deterministic arbiter.",
        user=json.dumps(arbiter_payload),
    )
    try:
        arbiter_decision = parse_json_response(arbiter_text, ArbiterDecision)
    except Exception:
        arbiter_decision = ArbiterDecision(
            action="NO_TRADE",
            symbol=None,
            entry=None,
            stop=None,
            targets=[],
            confidence=None,
            reason_codes=["ARBITER_PARSE_ERROR"],
            notes=None,
        )
    audit["arbiter_decision"] = arbiter_decision.model_dump()
    # Stage D: validate and size
    final_decision = validate_and_size(
        arbiter_decision,
        as_of=as_of,
        run_id=run_id,
        whitelist=whitelist,
        max_risk_usd=max_risk_usd,
        cash_usd=cash_usd,
    )
    # Stage E: one-trade-per-day lock enforcement
    # Determine trade date from final decision
    trade_date_ny = final_decision.trade_date_ny
    if final_decision.action == "TRADE":
        # Load existing lock
        state = load_trade_lock(trade_lock_path)
        if state and state.last_trade_date_ny == trade_date_ny:
            # Override to no trade due to lock
            final_decision = FinalDecision(
                decision_schema_version=final_decision.decision_schema_version,
                run_id=final_decision.run_id,
                generated_at=final_decision.generated_at,
                as_of=final_decision.as_of,
                whitelist=final_decision.whitelist,
                max_risk_usd=final_decision.max_risk_usd,
                cash_usd=final_decision.cash_usd,
                action="NO_TRADE",
                trade_date_ny=trade_date_ny,
                symbol=None,
                entry=None,
                stop=None,
                targets=[],
                shares=None,
                risk_usd=None,
                r_multiple=None,
                confidence=None,
                reason_codes=["ONE_TRADE_PER_DAY_LOCKED"],
                audit={},
            )
        else:
            # Save new lock
            save_trade_lock(trade_lock_path, TradeLockState(trade_date_ny, run_id))
    # Attach audit information to final decision
    final_decision.audit = audit
    return final_decision