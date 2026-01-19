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
    """Helper to construct a full set of RTH 15‑minute bars for a given day.

    Tests for the backtesting engine rely on a deterministic number of
    bars per trading day to satisfy the warmup requirement.  Regular
    Trading Hours (RTH) for US equities run from 09:30 to 16:00 local
    time.  With 15‑minute intervals this yields 26 bars starting at
    09:30 and ending at 15:45 inclusive.  This helper always
    constructs exactly 26 bars regardless of the length of ``prices``.

    Each element in ``prices`` corresponds to the bar's open and close;
    the high and low are set slightly above and below to create a
    candle.  If ``prices`` has fewer than 26 entries, the sequence is
    padded by repeating the last provided price (or zero if no prices
    supplied).  Bars beyond the length of ``prices`` therefore repeat
    the final price deterministically.  If more than 26 prices are
    supplied, the excess values are ignored.  The bar timestamp is
    computed as ``09:30 + i*15min`` in the given timezone and then
    converted to UTC.
    """
    bars: list[dict] = []
    # Determine padding value: use last price if provided, otherwise 0.0
    if prices:
        pad_price = prices[-1]
    else:
        pad_price = 0.0
    # Build extended price list to length 26
    extended_prices = list(prices) + [pad_price] * (26 - len(prices))
    # Trim to exactly 26 entries in case more were provided
    extended_prices = extended_prices[:26]
    # Compute base timestamp at 09:30 local time
    base_time = datetime.combine(trading_day, time(9, 30)).replace(tzinfo=ZoneInfo(tz))
    for i, price in enumerate(extended_prices):
        # Compute local timestamp incrementally by 15 minutes per bar
        ts_local = base_time + timedelta(minutes=15 * i)
        # Convert to UTC for storage
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
    # To satisfy warmup, prepend >=200 bars from prior trading days for each symbol.
    def _prepend_history(cur_day: date, base_prices: list[float]):
        """Build a list of bars for eight prior trading days to meet warmup."""
        history_bars: list[dict] = []
        added = 0
        prev_day = cur_day - timedelta(days=1)
        # We need at least 8 trading days (8*26=208 bars) to exceed 200 bars threshold.
        while added < 8:
            if prev_day.weekday() < 5:
                history_bars.extend(_make_bars_for_day(prev_day, base_prices))
                added += 1
            prev_day -= timedelta(days=1)
        return history_bars
    # Build bars for the current day
    cur_bars_aapl = _make_bars_for_day(day, prices)
    cur_bars_msft = _make_bars_for_day(day, prices)
    # Prepend warmup history
    hist_bars = _prepend_history(day, prices)
    bars_map = {
        "AAPL": hist_bars + cur_bars_aapl,
        "MSFT": hist_bars + cur_bars_msft,
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
    # Prepend warmup bars for previous trading days
    hist_bars = []
    added = 0
    prev_day = day - timedelta(days=1)
    while added < 8:
        if prev_day.weekday() < 5:
            hist_bars.extend(_make_bars_for_day(prev_day, prices))
            added += 1
        prev_day -= timedelta(days=1)
    bars_map = {"AAPL": hist_bars + _make_bars_for_day(day, prices)}
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
    # Prepend warmup bars from previous trading days
    hist_bars = []
    added = 0
    prev_day = day - timedelta(days=1)
    while added < 8:
        if prev_day.weekday() < 5:
            hist_bars.extend(_make_bars_for_day(prev_day, prices))
            added += 1
        prev_day -= timedelta(days=1)
    bars_map = {"AAPL": hist_bars + _make_bars_for_day(day, prices)}
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
    # Prepend warmup bars from previous trading days
    hist_bars = []
    added = 0
    prev_day = day - timedelta(days=1)
    base_prices = [100.0 + i * 0.05 for i in range(20)]
    while added < 8:
        if prev_day.weekday() < 5:
            hist_bars.extend(_make_bars_for_day(prev_day, base_prices))
            added += 1
        prev_day -= timedelta(days=1)
    bars_map = {"AAPL": hist_bars + bars_mod}
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
    # Prepend warmup bars for previous trading days to avoid warmup gating
    hist_bars = []
    added = 0
    prev_day = day - timedelta(days=1)
    # Use a simple flat price series for warmup to avoid influencing gating
    warmup_prices = [100 + i * 0.1 for i in range(26)]
    while added < 8:
        if prev_day.weekday() < 5:
            hist_bars.extend(_make_bars_for_day(prev_day, warmup_prices))
            added += 1
        prev_day -= timedelta(days=1)
    bars_map = {"AAPL": hist_bars + _make_bars_for_day(day, prices)}
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


def test_rth_filter_excludes_premarket():
    """Premarket bars should not influence RTH backtest decisions.

    Construct a synthetic trading day with a strong premarket uptrend
    followed by flat RTH bars.  The baseline strategy would normally
    interpret the uptrend as a candidate, but the RTH filter should
    exclude premarket bars and therefore produce no trade at the
    10:15 decision time.
    """
    # Use a weekday (Thursday) to ensure a trading day
    day = date(2025, 1, 9)
    tz = "America/New_York"
    zone = ZoneInfo(tz)
    bars: list[dict] = []
    # Construct premarket bars starting at 04:00 and ending before 09:30
    pre_start = datetime.combine(day, time(4, 0)).replace(tzinfo=zone)
    # 15‑minute intervals from 04:00 to 09:15 inclusive
    pre_prices = [50 + i * 2.0 for i in range(22)]  # strong uptrend
    for i, price in enumerate(pre_prices):
        ts_local = pre_start + i * timedelta(minutes=15)
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
    # Construct RTH bars from 09:30 to 10:30
    rth_start = datetime.combine(day, time(9, 30)).replace(tzinfo=zone)
    rth_prices = [pre_prices[-1]] * 5  # flat during RTH
    for i, price in enumerate(rth_prices):
        ts_local = rth_start + i * timedelta(minutes=15)
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
    # Prepend warmup bars for previous trading days to ensure warmup satisfied
    hist_bars: list[dict] = []
    added = 0
    prev_day = day - timedelta(days=1)
    # Use a flat price series for warmup bars
    warmup_prices = [50 + i * 0.1 for i in range(26)]
    while added < 8:
        if prev_day.weekday() < 5:
            hist_bars.extend(_make_bars_for_day(prev_day, warmup_prices))
            added += 1
        prev_day -= timedelta(days=1)
    bars_map = {"AAPL": hist_bars + bars}
    # Decision at 10:15 local; after RTH filtering, only RTH bars should remain
    cfg = BacktestConfig(
        symbols=["AAPL"],
        start_date=day,
        end_date=day,
        initial_cash=100_000.0,
        decision_time=time(10, 15),
    )
    engine = BacktestEngine(config=cfg)
    result = engine.run(bars_map)
    # No trade should occur due to absence of valid candidate when premarket is excluded
    assert len(result.trades) == 0
    assert result.reasons and result.reasons[0]["reason"] == "NO_VALID_CANDIDATE"


def test_cost_model_affects_pnl():
    """Cost model slippage and commission should reduce pnl deterministically.

    Construct a simple day where the baseline strategy enters a trade and exits
    at the end of the day.  Run the backtest with zero cost model and with
    a higher cost model and assert that the pnl from the cost model run is
    strictly less.  Additionally, verify that the per‑share pnl difference
    matches the expected slippage and commission adjustments.
    """
    day = date(2025, 1, 10)
    # Construct an increasing price series for the day.  The baseline strategy
    # will detect an uptrend and enter a trade.  Use enough bars to satisfy
    # indicator warmup (e.g., 20 bars).
    prices = [100 + i * 0.2 for i in range(20)]
    # Prepend warmup bars for previous trading days to satisfy warmup
    hist_bars = []
    added = 0
    prev_day = day - timedelta(days=1)
    while added < 8:
        if prev_day.weekday() < 5:
            hist_bars.extend(_make_bars_for_day(prev_day, prices))
            added += 1
        prev_day -= timedelta(days=1)
    bars_map = {"AAPL": hist_bars + _make_bars_for_day(day, prices)}
    # Common backtest configuration
    cfg = BacktestConfig(
        symbols=["AAPL"],
        start_date=day,
        end_date=day,
        initial_cash=100_000.0,
        decision_time=time(13, 30),
    )
    # Run with zero cost model
    engine_no_cost = BacktestEngine(config=cfg, cost_model=CostModel(slippage_bps=0.0, commission_per_share=0.0))
    result_no_cost = engine_no_cost.run(bars_map)
    # There should be exactly one trade
    assert len(result_no_cost.trades) == 1
    trade_no_cost = result_no_cost.trades[0]
    pnl_no_cost = trade_no_cost.pnl
    shares = trade_no_cost.shares
    entry_price = trade_no_cost.entry_price
    exit_price = trade_no_cost.exit_price
    # Run with higher slippage and commission
    slippage_bps = 10.0
    commission_per_share = 0.01
    engine_cost = BacktestEngine(
        config=cfg,
        cost_model=CostModel(slippage_bps=slippage_bps, commission_per_share=commission_per_share),
    )
    result_cost = engine_cost.run(bars_map)
    assert len(result_cost.trades) == 1
    trade_cost = result_cost.trades[0]
    pnl_cost = trade_cost.pnl
    # PnL with costs must be strictly less than without costs
    assert pnl_cost < pnl_no_cost
    # Compute expected per‑share difference: slippage adds to entry and subtracts from exit
    expected_diff_per_share = (
        entry_price * (slippage_bps / 10000.0)
        + exit_price * (slippage_bps / 10000.0)
        + 2 * commission_per_share
    )
    actual_diff_per_share = (pnl_no_cost - pnl_cost) / shares
    # Allow a small tolerance for floating‑point arithmetic
    assert abs(actual_diff_per_share - expected_diff_per_share) < 1e-6
