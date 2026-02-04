"""Backtesting package for JARVIS.

This package implements a deterministic, event‑driven intraday backtesting
engine along with a baseline mechanical strategy, cost model, metrics
computation and simple artifact writers.  The goal of the backtest
module is to mirror the constraints imposed on the live trading
pipeline: long‑only trades, a single trade per NY trading day,
whole‑share sizing, no leverage, end‑of‑day flat, and an allowlist
restriction.  All computations are causal and use only information
available up to the decision timestamp; no randomness is introduced
anywhere in the engine.

Submodules:

* ``engine`` – Event‑driven simulation orchestrating the trade cycle.
* ``strategy`` – Strategy interface and baseline deterministic implementation.
* ``costs`` – Cost model for slippage and commissions.
* ``metrics`` – PnL and summary statistics computation.
* ``io`` – Writing backtest artifacts (trades, equity curve, summary).
* ``splits`` – Helpers for trading day iteration and bar slicing.

"""

from .engine import BacktestEngine, BacktestResult  # noqa: F401
from .strategy import BacktestConfig, BaselineStrategy  # noqa: F401