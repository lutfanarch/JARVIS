"""Event‑driven intraday backtesting engine.

The backtest engine coordinates the simulation of a deterministic
intraday trading strategy.  It iterates over trading days between
configured start and end dates, constructs context at a specified
decision time, queries a strategy for a candidate trade, performs
position sizing, simulates entry and exit based on bar data, applies
costs and commissions, updates equity, and records trade and no‑trade
events.  After completion, it computes summary metrics and writes
artifacts via the I/O helpers.

This engine operates purely in memory: it expects that bars (OHLCV
data) have been loaded from a database or other source into in‑memory
lists keyed by symbol.  Bars must be sorted by timestamp ascending
and contain timezone‑aware UTC ``ts`` values.  No database or network
access occurs during a backtest run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date, time, timedelta
from typing import Dict, List, Any, Optional

from zoneinfo import ZoneInfo

from ..config import CANONICAL_WHITELIST
from .costs import CostModel
from .strategy import BacktestConfig, Strategy, BaselineStrategy, Candidate
from .splits import trading_days, bars_up_to, bars_after, filter_rth_bars, required_warmup_bars
from .metrics import Trade, equity_curve, compute_summary


@dataclass
class BacktestResult:
    """Container for completed backtest results."""

    trades: List[Trade]
    equity_curve: List[Dict[str, Any]]
    summary: Dict[str, Any]
    reasons: List[Dict[str, str]]


class BacktestEngine:
    """Engine for running deterministic intraday backtests."""

    def __init__(
        self,
        config: BacktestConfig,
        cost_model: Optional[CostModel] = None,
        strategy: Optional[Strategy] = None,
    ) -> None:
        self.config = config
        self.cost_model = cost_model or CostModel()
        self.strategy = strategy or BaselineStrategy()
        # Initialise safety counters
        self.days_paused_by_safety_pre_pause = 0
        self.trades_blocked_by_safety_pre_pause = 0

    def run(
        self,
        bars: Dict[str, List[Dict[str, Any]]],
    ) -> BacktestResult:
        """Run the backtest on the provided bar data.

        Parameters
        ----------
        bars : dict
            Mapping from symbol to list of 15‑minute bar dictionaries.

        Returns
        -------
        BacktestResult
            Trades, equity curve, summary metrics and no‑trade reasons.
        """
        # Validate bar keys
        for sym in self.config.symbols:
            if sym not in bars:
                raise ValueError(f"Missing bar data for symbol {sym}")
        # Pre‑sort bars per symbol by timestamp and filter to Regular Trading Hours
        sorted_bars: Dict[str, List[Dict[str, Any]]] = {}
        for sym, b_list in bars.items():
            # Remove bars without timestamps and sort ascending by ts
            sorted_raw = sorted(
                [b for b in b_list if b.get("ts")], key=lambda x: x["ts"]
            )
            # Apply RTH filtering based on the decision timezone; maintain order
            sorted_bars[sym] = filter_rth_bars(sorted_raw, self.config.decision_tz)
        # Determine list of trading days
        day_list = trading_days(self.config.start_date, self.config.end_date)
        # Prepare results
        trades: List[Trade] = []
        reasons: List[Dict[str, str]] = []
        cash = self.config.initial_cash
        equity = cash
        # Timezone objects
        local_tz = ZoneInfo(self.config.decision_tz)
        for d in day_list:
            # Initialize daily PnL
            daily_pnl = 0.0
            # Determine decision timestamp in UTC
            local_dt = datetime.combine(d, self.config.decision_time)
            local_dt = local_dt.replace(tzinfo=local_tz)
            decision_ts = local_dt.astimezone(ZoneInfo("UTC"))

            # Enforce daily pause limits
            # Safety pre-pause guardrail
            if self.config.safety_pre_pause_enabled:
                if daily_pnl <= -self.config.safety_pre_pause_limit_usd:
                    self.trades_blocked_by_safety_pre_pause += 1
                    # Treat as pause for the day
                    self.days_paused_by_safety_pre_pause += 1
                    reasons.append({"date": d.isoformat(), "reason": "TTP_SAFETY_PRE_PAUSE"})
                    continue

            # Strict daily pause limit
            if self.config.daily_pause_limit_usd is not None:
                if daily_pnl <= -self.config.daily_pause_limit_usd:
                    reasons.append({"date": d.isoformat(), "reason": "DAILY_PAUSE_LIMIT_HIT"})
                    continue

            # Evaluate each symbol using strategy, applying warmup gating
            candidates: List[Candidate] = []
            warmup_insufficient = False
            for sym in self.config.symbols:
                b15 = sorted_bars[sym]
                # Determine the bars up to the decision timestamp (inclusive)
                bars_before = bars_up_to(b15, decision_ts)
                # Require a minimum number of bars to avoid look‑ahead bias
                required = required_warmup_bars("15m")
                if len(bars_before) < required:
                    # Skip this symbol during warmup; mark that at least one symbol
                    # lacked sufficient history.  We do not attempt to generate a
                    # candidate for symbols without enough bars.
                    warmup_insufficient = True
                    continue
                # Bars may contain multiple days; we rely on strategy to
                # slice appropriately using decision_ts
                cand = self.strategy.generate_candidate(sym, b15, decision_ts, self.config)
                if cand is not None:
                    candidates.append(cand)
            if not candidates:
                # If any symbol was skipped due to insufficient warmup, record a
                # specific reason; otherwise fall back to generic no‑candidate.
                reason_code = "WARMUP_INSUFFICIENT_BARS" if warmup_insufficient else "NO_VALID_CANDIDATE"
                reasons.append({"date": d.isoformat(), "reason": reason_code})
                continue
            # Select best candidate by highest score; tie break by symbol order
            candidates.sort(key=lambda c: (-c.score, c.symbol))
            best = candidates[0]
            # Risk sizing
            risk_per_share = best.entry_price - best.stop_price
            if risk_per_share <= 0:
                reasons.append({"date": d.isoformat(), "reason": "NON_POSITIVE_RISK"})
                continue
            # Determine risk cap: min(percent of equity, fixed)
            equity = cash  # in this flat intraday model equity = cash
            risk_cap = min(self.config.risk_cap_pct * equity, self.config.risk_cap_fixed)
            max_shares_by_risk = risk_cap / risk_per_share
            max_shares_by_cash = cash / best.entry_price if best.entry_price > 0 else 0
            shares = int(max(0, min(max_shares_by_risk, max_shares_by_cash)))
            if shares < 1:
                reasons.append({"date": d.isoformat(), "reason": "INSUFFICIENT_SIZE"})
                continue
            # Simulate trade: evaluate exit conditions using subsequent bars
            b15_full = sorted_bars[best.symbol]
            # Find index of entry bar in the full bars list
            entry_index = None
            for idx, b in enumerate(b15_full):
                if b["ts"] == best.entry_ts:
                    entry_index = idx
                    break
            if entry_index is None:
                reasons.append({"date": d.isoformat(), "reason": "ENTRY_BAR_NOT_FOUND"})
                continue
            # Determine last bar index for this trading day
            # Bars may cross midnight; ensure we only consider bars with local date == d
            exit_price = None
            exit_ts = None
            exit_reason = "EOD"
            target_hit = False
            stop_hit = False
            # Determine entry price prior to cost adjustments
            entry_price_raw = best.entry_price
            # Evaluate following bars
            # Loop through bars after entry
            for j in range(entry_index + 1, len(b15_full)):
                bar = b15_full[j]
                ts = bar["ts"]
                bar_local_date = ts.astimezone(local_tz).date()
                if bar_local_date != d:
                    break  # reached next day
                low = bar["low"]
                high = bar["high"]
                # If both levels reached in same bar, decide deterministic rule: stop first
                if low <= best.stop_price and high >= best.target_price:
                    exit_price = best.stop_price
                    exit_ts = ts
                    exit_reason = "STOP_HIT"
                    stop_hit = True
                    break
                # Check target hit
                if high >= best.target_price:
                    exit_price = best.target_price
                    exit_ts = ts
                    exit_reason = "TARGET_HIT"
                    target_hit = True
                    break
                # Check stop hit
                if low <= best.stop_price:
                    exit_price = best.stop_price
                    exit_ts = ts
                    exit_reason = "STOP_HIT"
                    stop_hit = True
                    break
            if exit_price is None:
                # No stop or target; exit at last bar close of the day
                # Find last bar for this day
                last_idx = entry_index
                for j in range(entry_index, len(b15_full)):
                    bar = b15_full[j]
                    ts = bar["ts"]
                    if ts.astimezone(local_tz).date() != d:
                        break
                    last_idx = j
                last_bar = b15_full[last_idx]
                exit_price = last_bar["close"]
                exit_ts = last_bar["ts"]
                exit_reason = "EOD"
            # Apply costs to prices
            entry_price_adj = self.cost_model.apply_entry(entry_price_raw)
            exit_price_adj = self.cost_model.apply_exit(exit_price)
            commission = self.cost_model.total_commission(shares)
            # Compute raw PnL and R multiple
            pnl = (exit_price_adj - entry_price_adj) * shares - commission
            risk_value = risk_per_share * shares
            r_multiple = ((exit_price - entry_price_raw) / risk_per_share) if risk_per_share != 0 else 0.0
            # Update cash and daily PnL
            cash += pnl
            daily_pnl += pnl
            # Append trade record
            # Build trade record including Phase 3 regime details.  The candidate
            # context holds the regimes and the score used for tie‑breaking.
            tr = Trade(
                symbol=best.symbol,
                date=d.isoformat(),
                entry_ts=best.entry_ts.isoformat(),
                entry_price=entry_price_raw,
                shares=shares,
                stop_price=best.stop_price,
                target_price=best.target_price,
                exit_ts=exit_ts.isoformat() if exit_ts else "",
                exit_price=exit_price,
                exit_reason=exit_reason,
                pnl=pnl,
                risk=risk_value,
                r_multiple=r_multiple,
                score=float(getattr(best, "score", 0.0)),
                vol_regime_15m=str(best.context.get("vol_regime_15m", "")),
                trend_regime_1h=str(best.context.get("trend_regime_1h", "")),
            )
            trades.append(tr)
        # Compute equity curve and summary metrics.  The equity curve is
        # built over all trading days in the requested date range.  Once
        # the aggregate summary is computed, also compute a per‑symbol
        # breakdown so that downstream consumers can analyze performance
        # contributions from each symbol.  The per‑symbol breakdown
        # reuses the same date_strings to ensure curves are aligned.
        date_strings = [d.isoformat() for d in day_list]
        curve = equity_curve(trades, self.config.initial_cash, date_strings)
        summary = compute_summary(trades, curve)
        # Attach safety counters to summary
        summary["days_paused_by_safety_pre_pause"] = self.days_paused_by_safety_pre_pause
        summary["trades_blocked_by_safety_pre_pause"] = self.trades_blocked_by_safety_pre_pause
        # Compute per‑symbol summary using only the trades for each symbol
        from .metrics import compute_per_symbol_summary  # local import to avoid circular
        per_symbol = compute_per_symbol_summary(trades, self.config.initial_cash, date_strings)
        summary["per_symbol"] = per_symbol
        return BacktestResult(trades=trades, equity_curve=curve, summary=summary, reasons=reasons)
