"""Deterministic cost model for backtesting.

This module defines a simple slippage and commission model.  Slippage
is expressed in basis points (bps) per side and applied adversely
relative to the direction of the trade.  Commission is charged per
share per side; a flat commission is not included but could be added
easily.  Costs are applied at both entry and exit.

All cost functions operate on floats and integers and do not mutate
their inputs.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CostModel:
    """Simple cost model with slippage and per‑share commission.

    Parameters
    ----------
    slippage_bps : float
        Slippage in basis points (1 bp = 0.0001) applied per side.  A
        value of 2 means the entry price is increased by 0.02% and the
        exit price is decreased by 0.02% for a long position.
    commission_per_share : float
        Commission charged per share per side.  Commission is doubled
        since there is one charge at entry and one at exit.
    slippage_per_share : float
        Slippage in USD per share per side. If > 0, this overrides
        slippage_bps.
    """

    slippage_bps: float = 2.0
    commission_per_share: float = 0.0
    slippage_per_share: float = 0.0

    def apply_entry(self, price: float) -> float:
        """Apply slippage to an entry price for a long trade.

        A positive slippage value increases the effective entry price.
        """
        if self.slippage_per_share > 0.0:
            return price + self.slippage_per_share
        return price * (1.0 + self.slippage_bps / 10000.0)

    def apply_exit(self, price: float) -> float:
        """Apply slippage to an exit price for a long trade.

        A positive slippage value decreases the effective exit price.
        """
        if self.slippage_per_share > 0.0:
            return price - self.slippage_per_share
        return price * (1.0 - self.slippage_bps / 10000.0)

    def total_commission(self, shares: int) -> float:
        """Return total commission (entry + exit) for a trade.

        Commission is applied per share on both entry and exit.
        """
        return float(shares) * self.commission_per_share * 2.0
