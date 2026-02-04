"""Definition of proprietary trading firm evaluation profiles.

This module defines dataclasses that capture the parameters of a
proprietary trading firm's evaluation account.  Each profile
encodes the account size, drawdown limits, risk budgets and
execution rules that JARVIS should adhere to when sizing trades.  A
profile can be selected at runtime via the ``PROP_PROFILE``
environment variable.  When a profile is active the validator
enforces per‑trade risk limits, minimum profit per share and an
optional profit cap on the second target.  These gates are
deterministic: when a violation occurs the trade is vetoed into a
``NO_TRADE`` decision with a ``PROP_RULE_VIOLATION`` reason code.

To add a new profile, create an instance of :class:`PropFirmProfile`
below and register it in the ``_PROFILES`` dictionary.  See
``trade_the_pool_25k_beginner`` for a template.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional
import os


@dataclass(frozen=True)
class PropFirmProfile:
    """Represents a proprietary trading firm evaluation account.

    Attributes
    ----------
    name: str
        A unique identifier for this profile.  Set ``PROP_PROFILE`` to
        this name to activate the corresponding gates.
    account_size_usd: float
        The notional account size in USD used to compute percentage‑based
        budgets.
    profit_target_pct: float
        The percentage profit target required to pass the evaluation.
    daily_pause_pct: float
        Daily drawdown at which trading is paused, expressed as a
        percentage of the account size.
    max_loss_pct: float
        Maximum loss allowed over the life of the evaluation as a
        percentage of the account size.
    payout_split_trader_pct: float
        Percentage of profits paid out to the trader after passing the
        evaluation.
    max_position_profit_ratio: float
        Maximum ratio between a single position's profit and the profit
        target.  For Trade The Pool this is 0.30 (30%).
    min_profit_per_share_usd: float
        Minimum allowed profit per share (in USD) for any take‑profit
        level.  Targets implying a smaller per‑share profit will be
        vetoed.
    min_trade_duration_seconds: int
        Minimum hold time for any trade in seconds.  This is an
        execution note and is not enforced by the validator.
    per_trade_risk_pct: float
        Maximum risk per trade as a percentage of the account size.  The
        validator converts this into a USD amount and vetoes trades
        exceeding it.
    bot_daily_risk_cap_pct: float
        Daily risk cap as a percentage of the account size.  Currently
        unused but included for completeness.
    bot_max_loss_cap_pct: float
        Maximum loss cap for the evaluation as a percentage of the
        account size.  Currently unused but included for completeness.
    profit_cap_pct: float
        Optional cap on total profit allowed per trade as a percentage of
        the account size.  When the implied profit on the largest
        take‑profit level exceeds this cap the validator will reduce the
        outermost target or drop it entirely.

    default_take_profit_r: Optional[float]
        Default take‑profit multiple expressed in R (risk units).  When non‑``None``
        the validator will set the primary target to ``entry + default_take_profit_r * (entry - stop)``
        and update the R multiple accordingly.  This allows deterministic
        target placement consistent with the firm's evaluation rules.
        If ``None``, automatic target locking is disabled and the LLM‑proposed
        targets are used as‑is.

    # Note: ``profit_cap_pct`` still defines a total profit cap as a percentage of
    # the account size.  When the implied profit on the largest take‑profit
    # level exceeds this cap the validator will reduce the outermost target
    # or drop it entirely.  This attribute is independent of
    # ``default_take_profit_r``.
    """

    name: str
    account_size_usd: float
    profit_target_pct: float
    daily_pause_pct: float
    max_loss_pct: float
    payout_split_trader_pct: float
    max_position_profit_ratio: float
    min_profit_per_share_usd: float
    min_trade_duration_seconds: int
    per_trade_risk_pct: float
    bot_daily_risk_cap_pct: float
    bot_max_loss_cap_pct: float
    profit_cap_pct: float
    # Default take‑profit multiple; optional; placed after non‑default fields
    default_take_profit_r: Optional[float] = None

    @property
    def risk_budget_usd(self) -> float:
        """Maximum risk per trade in USD.

        Calculated by multiplying ``per_trade_risk_pct`` by the
        ``account_size_usd``.
        """
        return (self.per_trade_risk_pct / 100.0) * self.account_size_usd

    @property
    def daily_risk_cap_usd(self) -> float:
        """Daily risk cap in USD."""
        return (self.bot_daily_risk_cap_pct / 100.0) * self.account_size_usd

    @property
    def max_loss_cap_usd(self) -> float:
        """Maximum evaluation loss cap in USD."""
        return (self.bot_max_loss_cap_pct / 100.0) * self.account_size_usd

    @property
    def profit_cap_usd(self) -> float:
        """Maximum total profit allowed per trade in USD.

        This is used to shape the take‑profit levels by reducing the
        outermost target to respect the cap.
        """
        return (self.profit_cap_pct / 100.0) * self.account_size_usd


# Define built‑in profile for the Trade The Pool 25K beginner program.
TRADE_THE_POOL_25K_BEGINNER = PropFirmProfile(
    name="trade_the_pool_25k_beginner",
    account_size_usd=25_000.0,
    profit_target_pct=6.0,
    daily_pause_pct=2.0,
    max_loss_pct=4.0,
    payout_split_trader_pct=70.0,
    max_position_profit_ratio=0.30,
    min_profit_per_share_usd=0.10,
    min_trade_duration_seconds=30,
    # Risk per trade of $50 corresponds to 0.20% of a $25k account.
    per_trade_risk_pct=0.20,
    bot_daily_risk_cap_pct=0.60,
    bot_max_loss_cap_pct=3.0,
    profit_cap_pct=1.5,
    # Default take‑profit is 1.5R (consistent with evaluation guidelines).
    default_take_profit_r=1.5,
)

# Registry of all supported profiles keyed by name.  Add new profiles
# here when extending support to additional firms.
_PROFILES: Dict[str, PropFirmProfile] = {
    TRADE_THE_POOL_25K_BEGINNER.name: TRADE_THE_POOL_25K_BEGINNER,
}


def get_profile(name: str) -> Optional[PropFirmProfile]:
    """Return the profile with the given name or ``None`` if unknown."""
    return _PROFILES.get(name)


def get_active_profile() -> Optional[PropFirmProfile]:
    """Return the active profile based on the ``PROP_PROFILE`` environment.

    If ``PROP_PROFILE`` is not set or refers to an unknown profile,
    ``None`` is returned.
    """
    name = os.getenv("PROP_PROFILE")
    if not name:
        return None
    return get_profile(name)