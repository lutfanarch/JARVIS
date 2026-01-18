"""Tests for the backtesting engine and baseline strategy.

These tests construct small synthetic bar datasets to verify that the
backtesting engine enforces core constraints: at most one trade per
day, positions are flattened at end of day, trades use whole shares
and do not exceed available cash, stop/target resolution is
deterministic, and no look‑ahead bias is present in the strategy.

The synthetic bars are simple upward or downward ramps with constant
volatility to keep indicator computations deterministic.  The decision
time is set later in the trading day (e.g., 13:30 local) to ensure
enough bars are available for ATR calculation.
"""

from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from informer.backtest.engine import BacktestEngine
from informer.backtest.strategy import BacktestConfig
from informer.backtest.costs import CostModel


def _make_bars_for_day(trading_day: date, prices: list[float], tz: str = "America/New_York"):
    """Helper to construct a list of 15m bar dictionaries for a given day.

    Each price in ``prices`` corresponds to the bar's open and close;
    the high and low are set slightly above and below to create a
    candle.  The bar timestamp is computed as ``09:30 + i*15min`` in
    the given timezone and then converted to UTC.
    """
    bars = []
    base_time = datetime.combine(trading_day, time(9, 30)).replace(tzinfo=ZoneInfo(tz))
    for i, price in enumerate(prices):
        ts_local = base_time + i * timedelta(minutes=15)
        ts = ts_local.astimezone(ZoneInfo("UTC"))
        bars.append(
            {
                "ts": ts,
                "open": float(price),
                "high": float(price + 0.5),
                "low": float(price - 0.5),
                "close": float(price),
                "volume": 1000.0,
            }
        )
    return bars


def test_one_trade_per_day_enforced():
    # Two symbols with identical price ramps produce identical scores.
    # Engine should pick the alphabetically first symbol (AAPL) only.
    day = date(2025, 1, 2)
    prices = [50 + i * 0.1 for i in range(20)]  # monotonic up
    bars_map = {
        "AAPL": _make_bars_for_day(day, prices),
        "MSFT": _make_bars_for_day(day, prices),
    }
    cfg = BacktestConfig(
        symbols=["AAPL", "MSFT"],
        start_date=day,
        end_date=day,
        initial_cash=100_000.0,
        decision_time=time(13, 30),
    )
    engine = BacktestEngine(config=cfg)
    result = engine.run(bars_map)
    # Only one trade should be executed
    assert len(result.trades) == 1
    assert result.trades[0].symbol == "AAPL"


def test_eod_flatten_enforced():
    # Prices rise slowly so target is not hit and stop is far; exit at EOD.
    day = date(2025, 1, 3)
    prices = [100 + i * 0.05 for i in range(20)]  # gentle uptrend
    bars_map = {"AAPL": _make_bars_for_day(day, prices)}
    cfg = BacktestConfig(
        symbols=["AAPL"],
        start_date=day,
        end_date=day,
        initial_cash=100_000.0,
        decision_time=time(13, 30),
    )
    engine = BacktestEngine(config=cfg)
    result = engine.run(bars_map)
    assert len(result.trades) == 1
    tr = result.trades[0]
    assert tr.exit_reason == "EOD"
    # Exit timestamp should be the last bar of the day
    last_bar_ts = bars_map["AAPL"][-1]["ts"].isoformat()
    assert tr.exit_ts == last_bar_ts


def test_whole_shares_and_cash_only():
    # With small risk and limited cash, shares should be integer and not exceed cash.
    # Use a weekday (Tuesday) instead of Saturday to ensure trading day is included
    day = date(2025, 1, 7)
    prices = [100 + i * 0.1 for i in range(20)]
    bars_map = {"AAPL": _make_bars_for_day(day, prices)}
    cfg = BacktestConfig(
        symbols=["AAPL"],
        start_date=day,
        end_date=day,
        initial_cash=1_000.0,
        decision_time=time(13, 30),
    )
    engine = BacktestEngine(config=cfg)
    result = engine.run(bars_map)
    assert len(result.trades) == 1
    tr = result.trades[0]
    assert isinstance(tr.shares, int)
    assert tr.shares >= 1
    # Ensure trade cost does not exceed initial cash
    assert tr.entry_price * tr.shares <= cfg.initial_cash


def test_stop_vs_target_resolution_deterministic():
    # Construct bars such that the bar immediately after entry hits both stop and target.
    # Use a weekday (Wednesday) instead of Sunday to ensure trading day is included
    day = date(2025, 1, 8)
    # Pre‑decision prices flat.  Create at least 20 bars so that
    # indices 0–19 exist.  The decision at 13:30 (16th bar) means the
    # entry occurs at index 17 (13:45) and the first bar after entry
    # is at index 18 (14:00).
    prices = [100.0 + i * 0.05 for i in range(20)]
    bars = _make_bars_for_day(day, prices)
    # Modify the 19th bar (index 18) to have extreme high and low so
    # both stop and target are crossed.  Use the original ts of that bar.
    modified_bar = {
        "ts": bars[18]["ts"],
        "open": 100.0,
        "high": 1_000_000.0,
        "low": 0.0,
        "close": 100.0,
        "volume": 1000.0,
    }
    bars_mod = bars.copy()
    bars_mod[18] = modified_bar
    bars_map = {"AAPL": bars_mod}
    cfg = BacktestConfig(
        symbols=["AAPL"],
        start_date=day,
        end_date=day,
        initial_cash=100_000.0,
        decision_time=time(13, 30),
    )
    engine = BacktestEngine(config=cfg)
    result = engine.run(bars_map)
    assert len(result.trades) == 1
    tr = result.trades[0]
    # Exit reason should be STOP_HIT due to deterministic rule
    assert tr.exit_reason == "STOP_HIT"


def test_no_lookahead_guard():
    # Bars prior to decision trend downward; after decision there is a spike.
    # Strategy should not take a trade because the gating fails when using
    # only data up to the decision time.
    day = date(2025, 1, 6)
    # Downtrend before decision
    pre_prices = [100 - i * 0.2 for i in range(16)]
    # Upward spike after decision
    post_prices = [pre_prices[-1]] + [pre_prices[-1] + 5] * 4
    prices = pre_prices + post_prices
    bars_map = {"AAPL": _make_bars_for_day(day, prices)}
    cfg = BacktestConfig(
        symbols=["AAPL"],
        start_date=day,
        end_date=day,
        initial_cash=100_000.0,
        decision_time=time(13, 30),
    )
    engine = BacktestEngine(config=cfg)
    result = engine.run(bars_map)
    # No trade should occur due to downtrend gating
    assert len(result.trades) == 0
    assert result.reasons and result.reasons[0]["reason"] == "NO_VALID_CANDIDATE"
