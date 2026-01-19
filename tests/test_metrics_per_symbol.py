"""Test per-symbol metrics computation.

This module tests the compute_per_symbol_summary helper added in
Phase 3 for generating per-symbol performance summaries.  A small
synthetic list of Trade objects across multiple symbols is constructed
to verify that the helper returns a deterministic dictionary keyed
alphabetically by symbol, that trade counts are correct per symbol,
and that total PnL values match the sum of the component trades.
"""

from typing import List

from informer.backtest.metrics import Trade, compute_per_symbol_summary


def _make_trade(symbol: str, date: str, pnl: float, r_mult: float) -> Trade:
    """Helper to construct a minimal Trade with dummy fields.

    Only the fields required by compute_per_symbol_summary and
    compute_summary are populated.  Timestamps are arbitrary but
    deterministic; prices and sizes are dummy values.  The date
    string should be in ISO format (YYYY-MM-DD).
    """
    # Use noon UTC for entry and exit timestamps for determinism
    entry_ts = f"{date}T12:00:00+00:00"
    exit_ts = f"{date}T12:15:00+00:00"
    return Trade(
        symbol=symbol,
        date=date,
        entry_ts=entry_ts,
        entry_price=100.0,
        shares=1,
        stop_price=99.0,
        target_price=102.0,
        exit_ts=exit_ts,
        exit_price=100.0 + pnl,  # dummy exit price to reflect pnl
        exit_reason="EOD",
        pnl=pnl,
        risk=1.0,
        r_multiple=r_mult,
        score=1.0,
        vol_regime_15m="low",
        trend_regime_1h="uptrend",
    )


def test_compute_per_symbol_summary_returns_sorted_keys_and_correct_metrics() -> None:
    """Compute per‑symbol summary on synthetic trades and verify results.

    This test constructs trades for two symbols with known PnL and
    verifies that the returned dictionary is keyed alphabetically,
    contains the correct number of trades per symbol, and that the
    total PnL matches the sum of each symbol's trades.  The initial
    cash and date list are arbitrary but deterministic.
    """
    # Construct synthetic trades: 2 trades for AAPL, 1 trade for MSFT
    trades: List[Trade] = [
        _make_trade("AAPL", "2025-01-02", 100.0, 2.0),
        _make_trade("AAPL", "2025-01-03", 50.0, 1.0),
        _make_trade("MSFT", "2025-01-02", -20.0, -0.5),
    ]
    # Sorted list of trading dates spanning the trades
    dates = ["2025-01-01", "2025-01-02", "2025-01-03"]
    initial_cash = 100000.0
    result = compute_per_symbol_summary(trades, initial_cash, dates)
    # Keys should be sorted alphabetically: AAPL then MSFT
    assert list(result.keys()) == sorted(result.keys()), "per_symbol keys are not sorted"
    assert list(result.keys()) == ["AAPL", "MSFT"], "Unexpected symbol keys in result"
    # Verify trade counts and total PnL per symbol
    aapl_metrics = result.get("AAPL")
    msft_metrics = result.get("MSFT")
    assert aapl_metrics is not None and msft_metrics is not None
    assert aapl_metrics["trades"] == 2, "AAPL trade count incorrect"
    assert msft_metrics["trades"] == 1, "MSFT trade count incorrect"
    # Total PnL should match the sum of pnl values for each symbol
    assert aapl_metrics["total_pnl"] == 150.0, "AAPL total_pnl incorrect"
    assert msft_metrics["total_pnl"] == -20.0, "MSFT total_pnl incorrect"