"""Tests for the Informer features CLI command."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from click.testing import CliRunner
import sqlalchemy as sa

from informer.cli import cli
from informer.ingestion.bars import bars_table, metadata as bars_metadata
from informer.features.storage import features_snapshot_table, metadata as features_metadata


def test_cli_features_upserts_rows(monkeypatch) -> None:
    # Prepare an in-memory SQLite engine
    engine = sa.create_engine("sqlite:///:memory:")
    # Create necessary tables
    bars_metadata.create_all(engine, tables=[bars_table])
    features_metadata.create_all(engine, tables=[features_snapshot_table])
    # Insert some bars for AAPL 15m timeframe
    base = datetime(2025, 1, 1, 9, 30, tzinfo=timezone.utc)
    bars = []
    for i in range(10):
        ts = base + timedelta(minutes=15 * i)
        bars.append(
            {
                "symbol": "AAPL",
                "timeframe": "15m",
                "ts": ts,
                "open": float(i) + 1.0,
                "high": float(i) + 2.0,
                "low": float(i) + 0.5,
                "close": float(i) + 1.5,
                "volume": 100 + i,
                "vwap": None,
                "source": "test",
            }
        )
    with engine.begin() as conn:
        conn.execute(bars_table.insert().values(bars))
    # Monkeypatch engine builder to return sqlite engine
    monkeypatch.setattr("informer.cli._build_engine", lambda: engine)
    # Run features CLI
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "features",
            "--symbols",
            "AAPL",
            "--timeframes",
            "15m",
            "--start",
            "2025-01-01",
            "--end",
            "2025-01-02",
            "--feature-version",
            "test",
        ],
    )
    assert result.exit_code == 0
    # Verify at least one feature snapshot row was inserted
    with engine.connect() as conn:
        rows = conn.execute(features_snapshot_table.select()).fetchall()
        assert len(rows) >= 1
        # Check indicators_json keys for a row
        row = rows[0]
        # In SQLAlchemy 2.x row behaves like a tuple; access via _mapping for dict-like access
        indicators = row._mapping["indicators_json"]
        # Should contain all expected indicator keys
        for key in ["ema20", "ema50", "ema200", "rsi14", "atr14", "vwap"]:
            assert key in indicators
        # Regime labels should also be present
        assert "trend_regime" in indicators
        assert "vol_regime" in indicators


def test_cli_features_upserts_patterns(monkeypatch) -> None:
    """Verify that patterns_json is populated when TA‑Lib is available via dummy module."""
    import sys
    from types import ModuleType
    import numpy as np
    # Prepare dummy talib module
    dummy = ModuleType("talib")

    from informer.features.patterns import DEFAULT_PATTERNS

    def make_func(name: str):
        def func(open, high, low, close):  # type: ignore[no-untyped-def]
            # Simple deterministic pattern: 100 for even indices, -100 for odd
            vals = []
            for idx in range(len(open)):
                vals.append(100 if idx % 2 == 0 else -100)
            return np.array(vals, dtype=int)
        return func

    for pat in DEFAULT_PATTERNS:
        setattr(dummy, pat, make_func(pat))
    # Patch sys.modules to use dummy talib
    monkeypatch.setitem(sys.modules, "talib", dummy)
    # Prepare in-memory SQLite engine and insert bars
    engine = sa.create_engine("sqlite:///:memory:")
    bars_metadata.create_all(engine, tables=[bars_table])
    features_metadata.create_all(engine, tables=[features_snapshot_table])
    base = datetime(2025, 1, 1, 9, 30, tzinfo=timezone.utc)
    bars = []
    for i in range(6):
        ts = base + timedelta(minutes=15 * i)
        bars.append(
            {
                "symbol": "AAPL",
                "timeframe": "15m",
                "ts": ts,
                "open": float(i) + 1.0,
                "high": float(i) + 2.0,
                "low": float(i) + 0.5,
                "close": float(i) + 1.5,
                "volume": 100 + i,
                "vwap": None,
                "source": "test",
            }
        )
    with engine.begin() as conn:
        conn.execute(bars_table.insert().values(bars))
    # Monkeypatch engine builder to return sqlite engine
    monkeypatch.setattr("informer.cli._build_engine", lambda: engine)
    # Run features CLI
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "features",
            "--symbols",
            "AAPL",
            "--timeframes",
            "15m",
            "--start",
            "2025-01-01",
            "--end",
            "2025-01-02",
            "--feature-version",
            "test",
        ],
    )
    assert result.exit_code == 0
    # Verify patterns_json is populated with keys when dummy talib is available
    with engine.connect() as conn:
        rows = conn.execute(features_snapshot_table.select()).fetchall()
        assert len(rows) >= 1
        row = rows[0]
        patterns = row._mapping["patterns_json"]
        assert patterns  # should not be empty
        # All default patterns should be present
        assert set(patterns.keys()) == set(DEFAULT_PATTERNS)
        # Values should be ints (100 or -100)
        for v in patterns.values():
            assert isinstance(v, int)
            assert v in (100, -100)