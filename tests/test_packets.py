"""Tests for the informer packet builder.

This module verifies that the packet builder produces correct output
structures under various conditions.  It uses an in-memory SQLite
database and temporary directories to avoid external dependencies.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, date
from pathlib import Path

import sqlalchemy as sa

from informer.packets.builder import build_informer_packet
from informer.packets.models import InformerPacket
from informer.ingestion.bars import bars_table, metadata as bars_metadata
from informer.features.storage import features_snapshot_table, metadata as features_metadata
from informer.ingestion.corporate_actions import corporate_actions_table, metadata as ca_metadata


def _create_min_png(path: Path) -> None:
    """Write a minimal PNG header to the given file path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")


def test_build_packet_happy_path(tmp_path) -> None:
    """Build a packet with bars, features and charts present and ensure status is OK."""
    # Set up in-memory SQLite engine and create tables
    engine = sa.create_engine("sqlite:///:memory:")
    bars_metadata.create_all(engine, tables=[bars_table])
    features_metadata.create_all(engine, tables=[features_snapshot_table])
    ca_metadata.create_all(engine, tables=[corporate_actions_table])
    # Insert synthetic bars for AAPL across three timeframes
    symbol = "AAPL"
    tf_list = ["15m", "1h", "1d"]
    with engine.begin() as conn:
        for tf in tf_list:
            bars = []
            # Choose base date and increments
            if tf == "15m":
                base = datetime(2025, 1, 1, 9, 30, tzinfo=timezone.utc)
                delta = timedelta(minutes=15)
            elif tf == "1h":
                base = datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc)
                delta = timedelta(hours=1)
            else:  # daily
                base = datetime(2024, 12, 28, 0, 0, tzinfo=timezone.utc)
                delta = timedelta(days=1)
            for i in range(5):
                ts = base + delta * i
                bars.append(
                    {
                        "symbol": symbol,
                        "timeframe": tf,
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
    # Insert features snapshot row for each timeframe at latest ts
    feature_version = "test"
    with engine.begin() as conn:
        for tf in tf_list:
            # Determine latest ts
            if tf == "15m":
                latest_ts = datetime(2025, 1, 1, 9, 30, tzinfo=timezone.utc) + timedelta(minutes=15 * 4)
            elif tf == "1h":
                latest_ts = datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc) + timedelta(hours=4)
            else:
                latest_ts = datetime(2024, 12, 28, 0, 0, tzinfo=timezone.utc) + timedelta(days=4)
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
            patterns_json = {}
            conn.execute(
                features_snapshot_table.insert().values(
                    symbol=symbol,
                    timeframe=tf,
                    ts=latest_ts,
                    indicators_json=indicators_json,
                    patterns_json=patterns_json,
                    feature_version=feature_version,
                )
            )
    # Create dummy charts
    charts_dir = tmp_path / "charts"
    chart_version = "v0.1"
    for tf in tf_list:
        file_path = charts_dir / chart_version / symbol / f"{tf}.png"
        _create_min_png(file_path)
    # Build packet
    as_of = datetime(2025, 1, 2, 0, 0, tzinfo=timezone.utc)
    packet = build_informer_packet(
        engine=engine,
        symbol=symbol,
        as_of=as_of,
        timeframes=tf_list,
        limit_bars=10,
        feature_version=feature_version,
        chart_version=chart_version,
        charts_dir=charts_dir,
        run_id="testrun",
        schema_version="v0.1",
        render_missing_charts=True,
    )
    assert isinstance(packet, InformerPacket)
    assert packet.status == "OK"
    # Check per timeframe
    for tf in tf_list:
        tf_pkt = packet.timeframes.get(tf)
        assert tf_pkt is not None
        assert tf_pkt.bars  # bars should not be empty
        # Bars should be sorted ascending
        ts_values = [b.ts for b in tf_pkt.bars]
        assert ts_values == sorted(ts_values)
        # Latest bar should be last bar
        assert tf_pkt.latest_bar == tf_pkt.bars[-1]
        # QA must pass
        assert tf_pkt.qa.passed
        # Latest features should include regimes
        assert tf_pkt.latest_features.get("trend_regime") is not None
        assert tf_pkt.latest_features.get("vol_regime") is not None
        # Chart path must exist
        assert tf_pkt.chart_path is not None
        p = Path(tf_pkt.chart_path)
        assert p.exists()
        # Not ready reasons should be empty
        assert not tf_pkt.not_ready_reasons
    # Events should have the required keys
    assert "corporate_actions" in packet.events
    assert "earnings" in packet.events
    assert "macro" in packet.events


def test_build_packet_not_ready_missing_features(tmp_path) -> None:
    """If features are missing, packet status should be NOT_READY and reasons include MISSING_FEATURES."""
    engine = sa.create_engine("sqlite:///:memory:")
    bars_metadata.create_all(engine, tables=[bars_table])
    features_metadata.create_all(engine, tables=[features_snapshot_table])
    ca_metadata.create_all(engine, tables=[corporate_actions_table])
    symbol = "AAPL"
    tf_list = ["15m"]
    # Insert bars without features
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
    # Create chart file so missing chart is not the reason
    charts_dir = tmp_path / "charts"
    chart_version = "v0.1"
    file_path = charts_dir / chart_version / symbol / "15m.png"
    _create_min_png(file_path)
    # Build packet without features
    as_of = datetime(2025, 1, 2, 0, 0, tzinfo=timezone.utc)
    packet = build_informer_packet(
        engine=engine,
        symbol=symbol,
        as_of=as_of,
        timeframes=tf_list,
        limit_bars=10,
        feature_version="test",
        chart_version=chart_version,
        charts_dir=charts_dir,
        run_id="testrun",
        schema_version="v0.1",
        render_missing_charts=True,
    )
    assert packet.status == "NOT_READY"
    # Timeframe should have missing features reason
    tf_pkt = packet.timeframes.get("15m")
    assert tf_pkt is not None
    assert "MISSING_FEATURES" in tf_pkt.not_ready_reasons