"""Strategy interface and baseline implementation for backtesting.

The strategy module defines an abstract interface for producing trade
candidates based on intraday bar data as well as a concrete baseline
strategy that follows deterministic rules.  The baseline strategy
uses causal indicators and regime information computed from the
existing ``informer.features`` package.  Parameters are provided
through a configuration object so they can be tuned in future work
without changing the core logic.

The strategy itself does not decide position sizing or apply costs;
those responsibilities reside in the engine.  Candidates produced by
the strategy include the entry/exit prices and a score used to rank
symbols.  A candidate is considered valid only if all gating
conditions are satisfied and the computed score exceeds a minimum
threshold.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, date, time
from typing import List, Optional, Dict, Any

from zoneinfo import ZoneInfo

from ..config import CANONICAL_WHITELIST
from ..features.indicators import compute_indicators
from ..features.regimes import compute_regimes
from .splits import aggregate_bars


@dataclass
class BacktestConfig:
    """Configuration parameters for a backtest run.

    The configuration holds all tunable parameters used by the
    backtesting engine and the baseline strategy.  Default values
    follow conservative risk and slippage assumptions.  Users may
    override these via CLI options.

    Attributes
    ----------
    symbols : list of str
        Symbols to consider for trading.  Must be a subset of
        ``CANONICAL_WHITELIST``.  Order of symbols influences tie
        breaking when scores are equal (sorted ascending by default).
    start_date : date
        First trading day of the backtest.
    end_date : date
        Last trading day of the backtest.
    initial_cash : float
        Starting cash balance in USD.
    decision_time : time
        The local time (in decision_tz) when trade decisions are
        evaluated each day.  Typically "10:15".
    decision_tz : str
        Timezone string for ``decision_time``, e.g., "America/New_York".
    k_stop : float
        Multiple of ATR14 used to set the stop price distance.
    k_target : float
        Multiple of ATR14 used to set the target price distance.
    score_threshold : float
        Minimum required score to take a trade.  If the computed
        score for a symbol is below this threshold, the symbol is
        skipped.
    risk_cap_pct : float
        Maximum fraction of equity to risk per trade (e.g., 0.005 for
        0.5%).  The smaller of ``risk_cap_pct * equity`` and
        ``risk_cap_fixed`` is used.
    risk_cap_fixed : float
        Maximum dollar amount to risk per trade.
    ``extra_params`` : dict
        A place to store additional strategy parameters not explicitly
        enumerated.  These values are persisted in the summary
        artifact.
    """

    symbols: List[str]
    start_date: date
    end_date: date
    initial_cash: float = 100_000.0
    decision_time: time = time(10, 15)
    decision_tz: str = "America/New_York"
    k_stop: float = 1.5
    k_target: float = 3.0
    score_threshold: float = 0.0
    risk_cap_pct: float = 0.005  # 0.5% of equity
    risk_cap_fixed: float = 1_000.0
    extra_params: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Enforce symbol subset of whitelist
        invalid = [s for s in self.symbols if s not in CANONICAL_WHITELIST]
        if invalid:
            raise ValueError(f"Symbols not in canonical whitelist: {invalid}")
        # Ensure sorted symbol order for deterministic tie breaking
        self.symbols = sorted(set(self.symbols))


@dataclass
class Candidate:
    """Represents a potential trade opportunity for a single symbol.

    Attributes
    ----------
    symbol : str
        The symbol under consideration.
    decision_ts : datetime
        The UTC timestamp corresponding to the decision time for the day.
    entry_ts : datetime
        Timestamp (UTC) of the next bar open when the trade is entered.
    entry_price : float
        The raw entry price (before costs) taken from the bar open.
    stop_price : float
        Absolute price level for the stop.  For a long position, this is
        ``entry_price - k_stop * atr14``.
    target_price : float
        Absolute price level for the target.  For a long position, this is
        ``entry_price + k_target * atr14``.
    score : float
        Numeric score used to rank candidates.  Higher is better.
    context : dict
        Additional metadata useful for debugging and analysis.  Includes
        the raw indicators and regimes used in the computation.
    """

    symbol: str
    decision_ts: datetime
    entry_ts: datetime
    entry_price: float
    stop_price: float
    target_price: float
    score: float
    context: Dict[str, Any]


class Strategy:
    """Abstract base class for strategies.

    Strategies must implement the ``generate_candidate`` method.  The
    engine will call this method for each symbol and trading day to
    produce at most one candidate per symbol.  A ``None`` return value
    means no trade should be taken for that symbol on the given day.
    """

    def generate_candidate(
        self,
        symbol: str,
        bars15: List[Dict[str, Any]],
        decision_ts: datetime,
        config: BacktestConfig,
    ) -> Optional[Candidate]:
        raise NotImplementedError


class BaselineStrategy(Strategy):
    """Baseline mechanical strategy using 15m and 1h indicators.

    The rules implemented here follow a simple trend/volatility gate and
    compute a relative strength score.  Only symbols whose latest
    1‑hour trend regime is ``uptrend`` and whose 15‑minute volatility
    regime is not ``high`` are considered.  Scores are computed
    deterministically from the last 15‑minute bar prior to the decision
    timestamp; if the score does not meet the configured threshold the
    candidate is rejected.
    """

    def generate_candidate(
        self,
        symbol: str,
        bars15: List[Dict[str, Any]],
        decision_ts: datetime,
        config: BacktestConfig,
    ) -> Optional[Candidate]:
        # Require at least two bars so we have a bar after the decision
        if not bars15 or len(bars15) < 2:
            return None
        # bars15 must be sorted by ts ascending.  Identify bars up to decision_ts.
        # Last bar at index <= decision_ts is used for indicator context; next
        # bar is the entry bar.
        last_idx = None
        for i, b in enumerate(bars15):
            if b["ts"] <= decision_ts:
                last_idx = i
            else:
                break
        if last_idx is None or last_idx < 0 or last_idx + 1 >= len(bars15):
            return None  # no bar after decision or no pre‑decision bar
        # Slice bars up to last_idx inclusive for indicator computation
        bars_up_to_dec = bars15[: last_idx + 1]
        # Compute 15m indicators and regimes
        indicators15 = compute_indicators(bars_up_to_dec, "15m")
        regimes15 = compute_regimes(bars_up_to_dec, indicators15, "15m")
        # Compute aggregated 1h bars and corresponding indicators/regimes
        bars1h = aggregate_bars(bars_up_to_dec, freq_minutes=60)
        indicators1h = compute_indicators(bars1h, "1h")
        regimes1h = compute_regimes(bars1h, indicators1h, "1h")
        if not indicators15 or not regimes15 or not indicators1h or not regimes1h:
            return None
        # Use last regimes
        trend15 = regimes15[-1]["vol_regime"]  # 15m vol regime stored as vol_regime
        trend1h = regimes1h[-1]["trend_regime"]
        # Gating: 1h trend must be uptrend; vol regime must not be high
        if trend1h != "uptrend":
            return None
        if trend15 == "high":
            return None
        # Extract last indicator values
        ind_last = indicators15[-1]
        atr14 = ind_last.get("atr14")
        ema20 = ind_last.get("ema20")
        ema50 = ind_last.get("ema50")
        vwap = ind_last.get("vwap")
        close = bars_up_to_dec[-1]["close"]
        # Require non‑null ATR and VWAP
        if atr14 is None or atr14 <= 0 or vwap is None:
            return None
        # Compute score: normalized momentum + distance from VWAP
        try:
            score = ((ema20 or 0) - (ema50 or 0)) / atr14 + (close - vwap) / atr14
        except Exception:
            return None
        if score < config.score_threshold:
            return None
        # Determine entry bar (next bar after decision)
        entry_bar = bars15[last_idx + 1]
        entry_price = entry_bar["open"]
        entry_ts = entry_bar["ts"]
        # Compute stop and target using ATR14 at decision
        stop_price = entry_price - config.k_stop * atr14
        target_price = entry_price + config.k_target * atr14
        # Build context for audit
        context = {
            "ema20": ema20,
            "ema50": ema50,
            "atr14": atr14,
            "vwap": vwap,
            "vol_regime_15m": trend15,
            "trend_regime_1h": trend1h,
        }
        return Candidate(
            symbol=symbol,
            decision_ts=decision_ts,
            entry_ts=entry_ts,
            entry_price=float(entry_price),
            stop_price=float(stop_price),
            target_price=float(target_price),
            score=float(score),
            context=context,
        )
