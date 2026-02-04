"""Abstract base class for market data providers.

This module defines the interface that all data providers must implement.
Providers return normalized data structures defined in
:mod:`informer.providers.models`.
"""

from __future__ import annotations

import abc
from datetime import datetime, date
from typing import Dict, Iterable, List

from .models import Bar, CorporateAction


class DataProvider(abc.ABC):
    """Interface for market data providers."""

    @abc.abstractmethod
    def get_historical_bars(
        self,
        symbols: Iterable[str],
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> List[Bar]:
        """Fetch historical bars for the given symbols over a date range.

        Args:
            symbols: Iterable of ticker symbols.
            timeframe: A string such as "1m", "15m", "1h", "1d".
            start: Start of the requested period (inclusive).
            end: End of the requested period (exclusive or inclusive depending on provider).

        Returns:
            A list of :class:`Bar` instances in chronological order.  Implementations may
            choose to return bars sorted across symbols or grouped by symbol; consumers
            should group by symbol if needed.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def get_latest_bars(
        self, symbols: Iterable[str], timeframe: str
    ) -> Dict[str, Bar]:
        """Fetch the latest bar for each symbol.

        Args:
            symbols: Iterable of ticker symbols.
            timeframe: A string such as "1m", "15m", "1h", "1d".

        Returns:
            A dictionary mapping each symbol to its latest :class:`Bar`.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def get_corporate_actions(
        self, symbols: Iterable[str], start: date, end: date
    ) -> List[CorporateAction]:
        """Fetch corporate action announcements for the given symbols.

        Args:
            symbols: Iterable of ticker symbols.
            start: Start date of the query (inclusive).
            end: End date of the query (inclusive).

        Returns:
            A list of :class:`CorporateAction` instances.
        """
        raise NotImplementedError