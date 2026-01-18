"""Metrics computation for backtest results.

This module contains helper functions to compute summary statistics and
equity curves from a list of executed trades.  Metrics are
deterministic and derived only from the trade data supplied.  A
minimal set of common performance indicators is returned; more
statistics can be added in future iterations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Dict, Any, Optional


@dataclass
class Trade:
    """Container for trade details used in metrics computation.

    In Phase 3 the trade record has been extended to include the
    decision score and regime classifications at the time of entry.
    These additional fields provide richer analysis and support the
    regime breakdown reporting required by the validation harness.
    """

    # Core trade details
    symbol: str
    date: str  # ISO local date of trade decision (YYYY‑MM‑DD)
    entry_ts: str
    entry_price: float
    shares: int
    stop_price: float
    target_price: float
    exit_ts: str
    exit_price: float
    exit_reason: str
    pnl: float
    risk: float
    r_multiple: float
    # Phase 3 additions
    score: float = 0.0
    vol_regime_15m: str = ""
    trend_regime_1h: str = ""


def equity_curve(trades: List[Trade], start_cash: float, dates: List[str]) -> List[Dict[str, Any]]:
    """Compute daily equity curve from trades and starting cash.

    The equity on each day is computed by adding the starting cash to
    the cumulative PnL of all trades executed up to and including that
    day.  Equity values are aligned to the input list of date strings.

    Parameters
    ----------
    trades : list of Trade
        Executed trades with date and pnl attributes.
    start_cash : float
        Initial cash balance.
    dates : list of str
        Sorted list of trading day strings in ISO format (YYYY‑MM‑DD).

    Returns
    -------
    list of dict
        Each dict contains 'date' and 'equity' keys.
    """
    equity = start_cash
    cumulative: Dict[str, float] = {d: 0.0 for d in dates}
    for tr in trades:
        cumulative[tr.date] = cumulative.get(tr.date, 0.0) + tr.pnl
    curve: List[Dict[str, Any]] = []
    running_pnl = 0.0
    for d in dates:
        running_pnl += cumulative.get(d, 0.0)
        curve.append({"date": d, "equity": equity + running_pnl})
    return curve


def compute_summary(trades: List[Trade], curve: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute summary performance metrics from trades and equity curve.

    This extended version provides additional statistics required for
    Phase 3 validation, including expectancy (alias of average R
    multiple), profit factor, win/loss averages, dispersion measures
    and drawdown expressed as a percentage of the prior equity peak.
    The computations are deterministic and use population statistics
    where applicable.

    Parameters
    ----------
    trades : list of Trade
        Executed trades.
    curve : list of dict
        Equity curve as returned by :func:`equity_curve`.

    Returns
    -------
    dict
        Summary metrics including trade count, win rate, totals and
        averages, dispersion metrics and drawdown statistics.
    """
    import statistics

    total_trades = len(trades)
    wins = [tr for tr in trades if tr.pnl > 0]
    losses = [tr for tr in trades if tr.pnl <= 0]
    win_rate = len(wins) / total_trades if total_trades else 0.0
    total_pnl = sum(tr.pnl for tr in trades)
    avg_pnl = total_pnl / total_trades if total_trades else 0.0
    avg_r = sum(tr.r_multiple for tr in trades) / total_trades if total_trades else 0.0
    # Expectancy is defined identically to average R multiple for backward compat
    expectancy_r = avg_r
    # Profit factor: ratio of total wins to absolute total losses
    total_win_pnl = sum(tr.pnl for tr in wins)
    total_loss_pnl = sum(tr.pnl for tr in losses)
    profit_factor: Any
    profit_factor_infinite = False
    if total_trades == 0 or len(losses) == 0:
        # No losses – profit factor is undefined/infinite
        profit_factor = None
        profit_factor_infinite = True
    else:
        profit_factor = total_win_pnl / abs(total_loss_pnl) if total_loss_pnl != 0 else None
        profit_factor_infinite = False if total_loss_pnl != 0 else True
    # Average win and loss PnL
    avg_win_pnl = total_win_pnl / len(wins) if wins else 0.0
    avg_loss_pnl = total_loss_pnl / len(losses) if losses else 0.0
    # Dispersion metrics on R multiples and pnl
    r_values = [tr.r_multiple for tr in trades]
    pnl_values = [tr.pnl for tr in trades]
    median_r = statistics.median(r_values) if r_values else 0.0
    min_r = min(r_values) if r_values else 0.0
    max_r = max(r_values) if r_values else 0.0
    pnl_std = statistics.pstdev(pnl_values) if len(pnl_values) > 1 else 0.0
    r_std = statistics.pstdev(r_values) if len(r_values) > 1 else 0.0
    # Max drawdown absolute and percentage relative to high water mark
    max_drawdown = 0.0
    max_drawdown_pct = 0.0
    high_water: Optional[float] = None
    for point in curve:
        eq = point["equity"]
        if high_water is None or eq > high_water:
            high_water = eq
        drawdown = (high_water - eq) if high_water is not None else 0.0
        if drawdown > max_drawdown:
            max_drawdown = drawdown
        if high_water and high_water > 0:
            dd_pct = drawdown / high_water
            if dd_pct > max_drawdown_pct:
                max_drawdown_pct = dd_pct
    return {
        "trades": total_trades,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "avg_pnl": avg_pnl,
        "avg_r": avg_r,
        "expectancy_r": expectancy_r,
        "profit_factor": profit_factor,
        "profit_factor_infinite": profit_factor_infinite,
        "avg_win_pnl": avg_win_pnl,
        "avg_loss_pnl": avg_loss_pnl,
        "median_r": median_r,
        "min_r": min_r,
        "max_r": max_r,
        "pnl_std": pnl_std,
        "r_std": r_std,
        "max_drawdown": max_drawdown,
        "max_drawdown_pct": max_drawdown_pct,
    }


def _aggregate_regime_stats(trades: List[Trade]) -> Dict[str, Any]:
    """Compute aggregated statistics for a list of trades.

    This helper computes a subset of the metrics used in the summary
    function.  It returns the number of trades, win rate, total pnl,
    average R multiple and profit factor (with infinite handling).
    """
    total = len(trades)
    wins = [tr for tr in trades if tr.pnl > 0]
    losses = [tr for tr in trades if tr.pnl <= 0]
    win_rate = len(wins) / total if total else 0.0
    total_pnl = sum(tr.pnl for tr in trades)
    avg_r = sum(tr.r_multiple for tr in trades) / total if total else 0.0
    total_win_pnl = sum(tr.pnl for tr in wins)
    total_loss_pnl = sum(tr.pnl for tr in losses)
    if total == 0 or len(losses) == 0:
        profit_factor = None
        profit_factor_infinite = True
    else:
        profit_factor = total_win_pnl / abs(total_loss_pnl) if total_loss_pnl != 0 else None
        profit_factor_infinite = False if total_loss_pnl != 0 else True
    return {
        "trades": total,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "avg_r": avg_r,
        "profit_factor": profit_factor,
        "profit_factor_infinite": profit_factor_infinite,
    }


def compute_regime_breakdown(trades: List[Trade]) -> Dict[str, Any]:
    """Compute regime breakdown statistics for the given trades.

    The breakdown is keyed by trend regime (1h), volatility regime
    (15m) and the combined tuple of (trend, vol).  Each entry
    contains aggregated metrics including trade count, win rate,
    total pnl, average R multiple and profit factor.  This function
    does not compute dispersion or drawdown metrics; it focuses on
    regime segmentation.

    Parameters
    ----------
    trades : list of Trade
        Executed trades containing regime annotations.

    Returns
    -------
    dict
        A nested mapping with keys "trend", "vol" and "combined"
        mapping to per‑bucket statistic dictionaries.
    """
    # Organize trades by regime keys
    trend_map: Dict[str, List[Trade]] = {}
    vol_map: Dict[str, List[Trade]] = {}
    combo_map: Dict[str, List[Trade]] = {}
    for tr in trades:
        trend = tr.trend_regime_1h or ""
        vol = tr.vol_regime_15m or ""
        trend_map.setdefault(trend, []).append(tr)
        vol_map.setdefault(vol, []).append(tr)
        combo_key = f"{trend}|{vol}"
        combo_map.setdefault(combo_key, []).append(tr)
    # Compute stats for each bucket
    breakdown: Dict[str, Any] = {
        "trend_regime_1h": {},
        "vol_regime_15m": {},
        "combined": {},
    }
    for k, v in trend_map.items():
        breakdown["trend_regime_1h"][k] = _aggregate_regime_stats(v)
    for k, v in vol_map.items():
        breakdown["vol_regime_15m"][k] = _aggregate_regime_stats(v)
    for k, v in combo_map.items():
        breakdown["combined"][k] = _aggregate_regime_stats(v)
    return breakdown
