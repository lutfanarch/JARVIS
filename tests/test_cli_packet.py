"""Tests for the informer packet CLI command."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from click.testing import CliRunner
import sqlalchemy as sa

from informer.cli import cli
from informer.ingestion.bars import bars_table, metadata as bars_metadata
from informer.features.storage import features_snapshot_table, metadata as features_metadata
from informer.ingestion.corporate_actions import corporate_actions_table, metadata as ca_metadata


def _write_min_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")


def test_cli_packet_generates_json(monkeypatch, tmp_path) -> None:
    """Invoke the packet CLI and verify a JSON file is written with expected keys."""
    # Prepare in-memory SQLite engine and create tables
    engine = sa.create_engine("sqlite:///:memory:")
    bars_metadata.create_all(engine, tables=[bars_table])
    features_metadata.create_all(engine, tables=[features_snapshot_table])
    ca_metadata.create_all(engine, tables=[corporate_actions_table])
    symbol = "AAPL"
    # Insert bars for one timeframe to keep the test simple
    base = datetime(2025, 1, 1, 9, 30, tzinfo=timezone.utc)
    with engine.begin() as conn:
        bars = []
        for i in range(5):
            ts = base + timedelta(minutes=15 * i)
            bars.append(
                {
                    "symbol": symbol,
                    "timeframe": "15m",
                    "ts": ts,
                    "open": 100 + i,
                    "high": 101 + i,
                    "low": 99 + i,
                    "close": 100.5 + i,
                    "volume": 1000 + i,
                    "vwap": None,
                    "source": "test",
                }
            )
        conn.execute(bars_table.insert().values(bars))
        # Insert features snapshot for latest ts
        latest_ts = base + timedelta(minutes=15 * 4)
        indicators_json = {
            "ema20": 1.0,
            "ema50": 2.0,
            "ema200": 3.0,
            "rsi14": 50.0,
            "atr14": 0.5,
            "vwap": 1.2,
            "trend_regime": "range",
            "vol_regime": "normal",
        }
        conn.execute(
            features_snapshot_table.insert().values(
                symbol=symbol,
                timeframe="15m",
                ts=latest_ts,
                indicators_json=indicators_json,
                patterns_json={},
                feature_version="test",
            )
        )
    # Create charts dir and write one PNG
    charts_dir = tmp_path / "charts"
    chart_version = "v0.1"
    _write_min_png(charts_dir / chart_version / symbol / "15m.png")
    # Monkeypatch engine builder to return sqlite engine
    monkeypatch.setattr("informer.cli._build_engine", lambda: engine)
    # Prepare CLI runner and run command
    runner = CliRunner()
    out_dir = tmp_path / "packets"
    result = runner.invoke(
        cli,
        [
            "packet",
            "--symbols",
            symbol,
            "--as-of",
            "2025-01-02T00:00:00Z",
            "--timeframes",
            "15m",
            "--limit",
            "10",
            "--out-dir",
            str(out_dir),
            "--charts-dir",
            str(charts_dir),
            "--feature-version",
            "test",
            "--chart-version",
            chart_version,
            "--run-id",
            "test",
        ],
    )
    assert result.exit_code == 0, result.output
    # Verify JSON file exists
    packet_path = out_dir / "test" / f"{symbol}.json"
    assert packet_path.exists()
    # Load and verify minimal structure
    with packet_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    assert data.get("schema_version") is not None
    assert data.get("symbol") == symbol
    assert data.get("status") in ("OK", "NOT_READY")
    assert isinstance(data.get("timeframes"), dict)
    # Corporate actions key should exist even if empty
    assert "corporate_actions" in data.get("events", {})