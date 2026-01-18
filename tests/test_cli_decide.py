"""Tests for the informer decide CLI command."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from click.testing import CliRunner
import pytest

from informer.cli import cli
from informer.llm.pipeline import _select_timeframe_for_symbol  # for constructing packet
from informer.packets.models import (
    InformerPacket,
    TimeframePacket,
    BarOut,
    QASummary,
)


def _write_packet_json(path: Path, packet: InformerPacket) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(packet.model_dump(), f, indent=2, sort_keys=True, default=str)


def _make_packet_for_cli(symbol: str) -> InformerPacket:
    base_ts = datetime(2025, 1, 1, 14, 30, tzinfo=timezone.utc)
    bars = [
        BarOut(
            ts=base_ts,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=1000,
            vwap=None,
            source="test",
        )
    ]
    tf_packet = TimeframePacket(
        timeframe="15m",
        bars=bars,
        latest_bar=bars[0],
        latest_features={
            "atr14": 0.5,
            "trend_regime": "uptrend",
            "vol_regime": "normal",
        },
        qa=QASummary(passed=True, errors=[], warnings=[]),
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
        timeframes={"15m": tf_packet},
        events={"corporate_actions": [], "earnings": [], "macro": []},
    )
    return packet


def test_cli_decide_creates_decision_file(monkeypatch, tmp_path) -> None:
    """Ensure the decide CLI writes a decision JSON file for a simple packet."""
    symbol = "AAPL"
    # Create packets directory and write one packet
    packets_dir = tmp_path / "packets"
    packet = _make_packet_for_cli(symbol)
    _write_packet_json(packets_dir / f"{symbol}.json", packet)
    # Set out directory
    out_dir = tmp_path / "decisions"
    # Prepare CLI runner
    runner = CliRunner()
    # Provide whitelist via environment
    monkeypatch.setenv("SYMBOLS", symbol)
    result = runner.invoke(
        cli,
        [
            "decide",
            "--symbols",
            symbol,
            "--packets-dir",
            str(packets_dir),
            "--out-dir",
            str(out_dir),
            "--run-id",
            "test",
            "--as-of",
            "2025-01-02T15:00:00Z",
            "--max-risk-usd",
            "50",
        ],
    )
    assert result.exit_code == 0, result.output
    # Decision file should exist
    decision_path = out_dir / "test.json"
    assert decision_path.exists(), f"Decision file {decision_path} not created"
    # Load decision JSON and check keys
    with decision_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    assert data.get("run_id") == "test"
    assert data.get("action") in ("TRADE", "NO_TRADE")
    assert "trade_date_ny" in data
    assert "reason_codes" in data