"""Tests for the Phase 2 decision pipeline.

These tests exercise the deterministic decision pipeline implemented
in ``informer.llm.pipeline``.  They ensure that the pipeline
produces sensible outcomes given various scenarios: when no
candidates are available, when a single trade passes through all
stages and sizing is computed correctly, and when the one-trade-per-
day lock prevents multiple trades on the same NY date.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from informer.llm.pipeline import run_decision_pipeline
from informer.llm.client import FakeLLMClient
from informer.llm.validator import validate_and_size
from informer.llm.state import load_trade_lock, save_trade_lock, TradeLockState
from informer.packets.models import (
    InformerPacket,
    TimeframePacket,
    BarOut,
    QASummary,
    QAEvent,
)

from zoneinfo import ZoneInfo


def _make_ok_packet(
    symbol: str,
    tf: str = "15m",
    latest_close: float = 100.0,
    atr14: float = 0.5,
    trend_regime: str = "uptrend",
    vol_regime: str = "normal",
    qa_passed: bool = True,
) -> InformerPacket:
    """Helper to create a minimal OK informer packet for testing."""
    # Build a small list of bars with ascending timestamps
    base_ts = datetime(2025, 1, 1, 14, 30, tzinfo=timezone.utc)
    bars: list[BarOut] = []
    for i in range(5):
        ts = base_ts + timedelta(minutes=15 * i)
        bars.append(
            BarOut(
                ts=ts,
                open=latest_close + i,
                high=latest_close + i + 1,
                low=latest_close + i - 1,
                close=latest_close + i,
                volume=1000 + i,
                vwap=None,
                source="test",
            )
        )
    latest_bar = bars[-1]
    tf_packet = TimeframePacket(
        timeframe=tf,
        bars=bars,
        latest_bar=latest_bar,
        latest_features={
            "atr14": atr14,
            "trend_regime": trend_regime,
            "vol_regime": vol_regime,
            # other indicators omitted
        },
        qa=QASummary(passed=qa_passed, errors=[], warnings=[]),
        chart_path=None,
        not_ready_reasons=[],
    )
    packet = InformerPacket(
        schema_version="v0.1",
        generated_at=base_ts,
        run_id="test",
        symbol=symbol,
        provider_version="alpaca-rest-v2",
        feature_version="test",
        chart_version="v0.1",
        status="OK",
        timeframes={tf: tf_packet},
        events={"corporate_actions": [], "earnings": [], "macro": []},
    )
    return packet


def test_no_trade_when_all_packets_not_ready(tmp_path) -> None:
    """Pipeline should produce NO_TRADE when all packets are NOT_READY."""
    packets = {
        "AAPL": None,
        "MSFT": None,
    }
    as_of = datetime(2025, 1, 2, 15, 0, tzinfo=timezone.utc)
    run_id = "test"
    whitelist = ["AAPL", "MSFT"]
    llm = FakeLLMClient()
    trade_lock_path = tmp_path / "trade_lock.json"
    decision = run_decision_pipeline(
        packets=packets,
        as_of=as_of,
        run_id=run_id,
        whitelist=whitelist,
        max_candidates=2,
        llm=llm,
        max_risk_usd=50.0,
        cash_usd=None,
        trade_lock_path=trade_lock_path,
    )
    assert decision.action == "NO_TRADE"
    # Reason codes should indicate no candidates
    assert "NO_CANDIDATES" in decision.reason_codes
    # Audit should include screener_output
    assert "screener_output" in decision.audit


def test_one_trade_max_and_sizing(tmp_path) -> None:
    """Pipeline should pick one trade and compute shares correctly."""
    # Create two OK packets; pipeline should choose first candidate deterministically
    packets = {
        "AAPL": _make_ok_packet("AAPL", latest_close=100.0, atr14=0.5, trend_regime="uptrend", vol_regime="normal"),
        "MSFT": _make_ok_packet("MSFT", latest_close=200.0, atr14=1.0, trend_regime="uptrend", vol_regime="normal"),
    }
    as_of = datetime(2025, 1, 2, 15, 0, tzinfo=timezone.utc)
    run_id = "test"
    whitelist = ["AAPL", "MSFT"]
    llm = FakeLLMClient()
    trade_lock_path = tmp_path / "lock.json"
    decision = run_decision_pipeline(
        packets=packets,
        as_of=as_of,
        run_id=run_id,
        whitelist=whitelist,
        max_candidates=2,
        llm=llm,
        max_risk_usd=50.0,
        cash_usd=None,
        trade_lock_path=trade_lock_path,
    )
    assert decision.action == "TRADE"
    assert decision.symbol in whitelist
    assert decision.shares is not None and decision.shares >= 1
    assert decision.risk_usd is not None and decision.risk_usd <= 50.0
    # Since AAPL appears first and meets criteria, it should be chosen
    assert decision.symbol == "AAPL"
    # Validate risk calculation: entry-stop = 0.5, risk_usd should be shares * 0.5
    risk_per_share = decision.entry - decision.stop  # type: ignore[operator]
    assert abs(decision.risk_usd - decision.shares * risk_per_share) < 1e-6


def test_trade_lock_blocks_second_trade_same_ny_day(tmp_path) -> None:
    """Pipeline should enforce one-trade-per-day lock."""
    packets = {
        "AAPL": _make_ok_packet("AAPL", latest_close=100.0, atr14=0.5, trend_regime="uptrend", vol_regime="normal"),
    }
    as_of = datetime(2025, 1, 2, 15, 0, tzinfo=timezone.utc)
    run_id = "test"
    whitelist = ["AAPL"]
    llm = FakeLLMClient()
    # Pre-create lock for the same NY date
    trade_lock_path = tmp_path / "state.json"
    # Determine NY date from as_of
    ny_date = as_of.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    save_trade_lock(trade_lock_path, TradeLockState(ny_date, "prev_run"))
    decision = run_decision_pipeline(
        packets=packets,
        as_of=as_of,
        run_id=run_id,
        whitelist=whitelist,
        max_candidates=2,
        llm=llm,
        max_risk_usd=50.0,
        cash_usd=None,
        trade_lock_path=trade_lock_path,
    )
    assert decision.action == "NO_TRADE"
    assert "ONE_TRADE_PER_DAY_LOCKED" in decision.reason_codes