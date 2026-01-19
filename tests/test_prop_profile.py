"""Tests for proprietary trading firm profile enforcement and sizing.

These tests verify that enabling the Trade The Pool evaluation profile
via ``PROP_PROFILE`` applies deterministic sizing and validity gates.
Under the locked policy for the 25k beginner program:

* The per‑trade risk budget is fixed at $50.  Passing a larger ``max_risk_usd``
  should have no effect; shares are sized off a $50 budget and the
  ``max_risk_usd`` field in the final decision reflects the capped amount.

* Trades are sized on a cash‑only basis.  When ``cash_usd`` is not
  provided the full account size ($25k) is used to cap the number of
  shares at ``floor(account_size / entry)``.

* The minimum profit per share gate still vetoes trades when the
  deterministic 1.5R target implies too small a profit per share (less
  than $0.10), even if the LLM originally proposed a different target.

* The primary target is locked to 1.5R.  Original LLM targets are
  preserved in the audit for traceability but ignored for sizing.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from informer.llm.models import ArbiterDecision
from informer.llm.validator import validate_and_size


def _make_arbiter(symbol: str, entry: float, stop: float, targets: list[float]) -> ArbiterDecision:
    return ArbiterDecision(
        action="TRADE",
        symbol=symbol,
        entry=entry,
        stop=stop,
        targets=targets,
        confidence=None,
        reason_codes=[],
        notes=None,
    )


def test_risk_budget_capping(monkeypatch) -> None:
    """Risk budget is capped at $50 even when a higher max_risk_usd is supplied."""
    monkeypatch.setenv("PROP_PROFILE", "trade_the_pool_25k_beginner")
    # Arbiter: risk per share = 1 (100 - 99), LLM target is irrelevant for sizing
    arbiter = _make_arbiter("AAPL", 100.0, 99.0, [102.0])
    # Provide a larger max_risk_usd (e.g. 100) to test capping to $50
    final = validate_and_size(
        arbiter,
        as_of=datetime(2025, 1, 2, 15, 0, tzinfo=timezone.utc),
        run_id="test-risk-cap",
        whitelist=["AAPL"],
        max_risk_usd=100.0,
        cash_usd=None,
    )
    # Should still produce a TRADE decision
    assert final.action == "TRADE", f"Expected TRADE, got {final.action}"
    # Shares sized by $50 risk: floor(50/1) = 50
    assert final.shares == 50, f"Shares should be 50, got {final.shares}"
    # Risk in USD computed from shares * risk per share
    assert final.risk_usd == 50.0
    # The max_risk_usd in the final decision should reflect the capped amount
    assert final.max_risk_usd == 50.0
    # Prop block should expose the profile risk budget ($50)
    assert final.prop is not None
    assert final.prop.get("risk_budget_usd") == pytest.approx(50.0)


def test_min_profit_per_share_gate(monkeypatch) -> None:
    """Trades are vetoed when the default 1.5R target implies profit < min_profit_per_share."""
    monkeypatch.setenv("PROP_PROFILE", "trade_the_pool_25k_beginner")
    # Use a very tight stop so that risk per share is small: 100 - 99.97 = 0.03
    arbiter = _make_arbiter("AAPL", 100.0, 99.97, [100.05])
    # With risk per share 0.03, the deterministic 1.5R target is entry + 1.5*0.03 = 100.045
    # Profit per share 0.045 is < min_profit_per_share 0.10 -> trade should be vetoed
    final = validate_and_size(
        arbiter,
        as_of=datetime(2025, 1, 2, 15, 0, tzinfo=timezone.utc),
        run_id="test-min-profit",
        whitelist=["AAPL"],
        max_risk_usd=50.0,
        cash_usd=None,
    )
    assert final.action == "NO_TRADE"
    assert "PROP_RULE_VIOLATION" in final.reason_codes
    # The violation type should be min_profit_per_share
    assert final.audit.get("prop_violation", {}).get("type") == "min_profit_per_share"


def test_default_take_profit_r(monkeypatch) -> None:
    """The primary target is locked to 1.5R and original LLM targets are recorded."""
    monkeypatch.setenv("PROP_PROFILE", "trade_the_pool_25k_beginner")
    # Provide LLM targets that would normally exceed the profit cap (e.g. [101.0, 110.0]),
    # but under the profile the target should be overridden to 1.5R: entry + 1.5 * (entry - stop) = 101.5.
    arbiter = _make_arbiter("AAPL", 100.0, 99.0, [101.0, 110.0])
    final = validate_and_size(
        arbiter,
        as_of=datetime(2025, 1, 2, 15, 0, tzinfo=timezone.utc),
        run_id="test-default-tp",
        whitelist=["AAPL"],
        max_risk_usd=50.0,
        cash_usd=None,
    )
    # Should be a valid trade
    assert final.action == "TRADE"
    # The targets list should contain exactly one entry: 1.5R above entry
    assert final.targets == [101.5]
    # r_multiple reflects the 1.5R multiple
    assert final.r_multiple == pytest.approx(1.5)
    # The original LLM targets should be present in the audit
    assert "llm_targets_original" in final.audit
    assert final.audit["llm_targets_original"] == [101.0, 110.0]
    # Profit cap should not adjust the deterministic target (profit = 75 < cap 375)
    assert "prop_target_adjustment" not in final.audit
    # Prop block still present
    assert final.prop is not None
    prop = final.prop
    # risk_budget_usd = 0.20% * 25000 = 50
    assert prop.get("risk_budget_usd") == pytest.approx(50.0)
    # profit_cap_usd = 1.5% * 25000 = 375
    assert prop.get("profit_cap_usd") == pytest.approx(375.0)
    # Check min profit per share and duration are exposed via prop block
    assert prop.get("min_profit_per_share_usd") == pytest.approx(0.10)
    assert prop.get("min_trade_duration_seconds") == 30


def test_cash_only_cap(monkeypatch) -> None:
    """When cash_usd is omitted, shares are capped by the account size (cash‑only)."""
    monkeypatch.setenv("PROP_PROFILE", "trade_the_pool_25k_beginner")
    # entry=100, stop=99.9 => risk_per_share=0.1; risk budget $50 allows shares_by_risk=500
    arbiter = _make_arbiter("AAPL", 100.0, 99.9, [102.0])
    final = validate_and_size(
        arbiter,
        as_of=datetime(2025, 1, 2, 15, 0, tzinfo=timezone.utc),
        run_id="test-cash-cap",
        whitelist=["AAPL"],
        max_risk_usd=50.0,
        cash_usd=None,
    )
    # Effective cash is the profile account size ($25k).  Cash cap = floor(25000 / 100) = 250
    assert final.shares == 250
    # Risk in USD = shares * risk per share = 250 * 0.1 = 25
    assert abs(final.risk_usd - 25.0) < 1e-6
    # max_risk_usd should be the capped risk budget ($50) and cash_usd should reflect account size
    assert final.max_risk_usd == 50.0
    assert final.cash_usd == 25_000.0
    # Prop block risk_budget_usd should be $50
    assert final.prop is not None
    assert final.prop.get("risk_budget_usd") == pytest.approx(50.0)