"""LLM analysis layer for the Informer project.

This package provides models and pipeline components for the Phase 2
analysis stage.  It abstracts the underlying language model via
``LLMClient`` and uses deterministic ``FakeLLMClient`` for offline
testing.  The pipeline orchestrates screening, multi‑agent evaluation,
arbitration, risk gating and trade lock enforcement.
"""

from .models import (
    DECISION_SCHEMA_VERSION_DEFAULT,
    ScreenerCandidate,
    ScreenerOutput,
    AnalystPlan,
    CriticReview,
    CandidateEvaluation,
    ArbiterDecision,
    FinalDecision,
)

from .client import LLMClient, FakeLLMClient, parse_json_response
from .pipeline import load_packets, run_decision_pipeline
from .validator import validate_and_size
from .state import TradeLockState, load_trade_lock, save_trade_lock

__all__ = [
    # version
    "DECISION_SCHEMA_VERSION_DEFAULT",
    # models
    "ScreenerCandidate",
    "ScreenerOutput",
    "AnalystPlan",
    "CriticReview",
    "CandidateEvaluation",
    "ArbiterDecision",
    "FinalDecision",
    # llm client
    "LLMClient",
    "FakeLLMClient",
    "parse_json_response",
    # pipeline functions
    "load_packets",
    "run_decision_pipeline",
    # validator
    "validate_and_size",
    # state
    "TradeLockState",
    "load_trade_lock",
    "save_trade_lock",
]