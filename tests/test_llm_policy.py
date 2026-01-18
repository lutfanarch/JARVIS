"""Tests for Phase 2 LLM provider policy and routing.

These unit tests verify that the multi‑LLM policy is correctly
enforced, that routing assigns calls to the appropriate provider, and
that failures in the critic stage result in conservative NO_TRADE
decisions.  The tests use dummy LLM clients to avoid network
requests and ensure deterministic behaviour.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

import pytest

from informer.llm.client import (
    LLMClient,
    FakeLLMClient,
    RoleRouterLLMClient,
)
from informer.llm.policy import ALLOWED_PROVIDERS, ROLE_ROUTING
from informer.llm.pipeline import run_decision_pipeline
from informer.packets.models import InformerPacket, TimeframePacket, BarOut, QASummary
from datetime import datetime, timezone


def _make_ok_packet(symbol: str) -> InformerPacket:
    """Create a minimal InformerPacket for testing the pipeline."""
    ts = datetime(2025, 1, 1, 14, 30, tzinfo=timezone.utc)
    bar = BarOut(
        ts=ts,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1000,
        vwap=None,
        source="test",
    )
    tf_packet = TimeframePacket(
        timeframe="15m",
        bars=[bar],
        latest_bar=bar,
        latest_features={"atr14": 1.0, "trend_regime": "uptrend", "vol_regime": "normal"},
        qa=QASummary(passed=True, errors=[], warnings=[]),
        chart_path=None,
        not_ready_reasons=[],
    )
    return InformerPacket(
        schema_version="v0.1",
        generated_at=ts,
        run_id="test",
        symbol=symbol,
        provider_version="alpaca-rest-v2",
        feature_version="test",
        chart_version="v0.1",
        status="OK",
        timeframes={"15m": tf_packet},
        events={"corporate_actions": [], "earnings": [], "macro": []},
    )


def test_validate_providers_rejects_unknown() -> None:
    """RoleRouterLLMClient should reject unknown provider names."""
    # Provide a client mapping containing an invalid provider
    clients: Dict[str, LLMClient] = {
        "openai": FakeLLMClient(),
        "foo": FakeLLMClient(),
    }
    with pytest.raises(ValueError):
        RoleRouterLLMClient(clients=clients)


def test_role_routing_assigns_correct_provider() -> None:
    """Role routing should map purposes to the expected provider names."""
    # Create dummy clients that return their provider name and purpose
    class DummyClient:
        def __init__(self, provider_name: str) -> None:
            self.provider_name = provider_name
        def complete(self, *, purpose: str, system: str, user: str) -> str:
            # Return a JSON string including provider and purpose for inspection
            return json.dumps({"provider": self.provider_name, "purpose": purpose})
    openai_client = DummyClient("openai")
    google_client = DummyClient("google")
    llm = RoleRouterLLMClient(clients={"openai": openai_client, "google": google_client})
    # Check each role in ROLE_ROUTING
    for purpose, expected_provider in ROLE_ROUTING.items():
        result_str = llm.complete(purpose=purpose, system="", user=json.dumps({}))
        result = json.loads(result_str)
        assert result["provider"] == expected_provider, f"Purpose {purpose} routed to {result['provider']} instead of {expected_provider}"


def test_pipeline_returns_no_trade_on_critic_failure(tmp_path: Path) -> None:
    """If the critic call raises an exception, the pipeline should yield NO_TRADE."""
    # Dummy critic that raises an error on complete
    class FailingCriticClient:
        def complete(self, *, purpose: str, system: str, user: str) -> str:
            raise RuntimeError("Simulated critic failure")
    # Use FakeLLMClient for OpenAI provider so screener and analyst behave normally
    openai_client = FakeLLMClient()
    google_client = FailingCriticClient()
    llm = RoleRouterLLMClient(clients={"openai": openai_client, "google": google_client}, fallback_critic=False)
    # Prepare packets with a single symbol that passes screener and analyst
    packets = {"AAPL": _make_ok_packet("AAPL")}
    as_of = datetime(2025, 1, 2, 15, 0, tzinfo=timezone.utc)
    run_id = "test_run"
    whitelist = ["AAPL"]
    # Run pipeline
    decision = run_decision_pipeline(
        packets=packets,
        as_of=as_of,
        run_id=run_id,
        whitelist=whitelist,
        max_candidates=2,
        llm=llm,
        max_risk_usd=50.0,
        cash_usd=None,
        trade_lock_path=tmp_path / "lock.json",
    )
    # Expect no trade due to critic failure
    assert decision.action == "NO_TRADE"
    assert "CRITIC_UNAVAILABLE" in decision.reason_codes
    # Audit should include screener_output and possibly evaluations
    assert "screener_output" in decision.audit