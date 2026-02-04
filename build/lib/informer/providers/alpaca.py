"""Alpaca market data provider implementation.

This module implements the :class:`DataProvider` interface for Alpaca's market
data REST API (version 2).  It supports fetching historical bars, latest bars
and corporate action announcements.  Responses are normalized into pydantic
models defined in :mod:`informer.providers.models`.

Environment variables:
    ALPACA_API_KEY_ID: Your Alpaca API key ID.
    ALPACA_API_SECRET_KEY: Your Alpaca API secret key.
    ALPACA_BASE_URL: Base URL for the Alpaca API (defaults to ``https://data.alpaca.markets``).
    ALPACA_DATA_FEED: Data feed to use (e.g., ``iex`` or ``sip``).
    ALPACA_ADJUSTMENT: Adjustment mode for bars (e.g., ``raw``, ``split``).
    ALPACA_LIMIT: Maximum number of bars returned per page.

Authentication failures will raise a :class:`RuntimeError`.  Transient errors
such as rate limits (HTTP 429) and server errors (5xx) are automatically
retried with exponential backoff.
"""

from __future__ import annotations

import os
from datetime import datetime, date, timezone, timedelta
from typing import Dict, Iterable, List, Optional, Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Stable provider version identifier used in informer packets.  Downstream
# code can reference this constant to record which data provider and API
# version were used to fetch bars, latest quotes and corporate actions.
PROVIDER_VERSION = "alpaca-rest-v2"

from .base import DataProvider
from .models import Bar, CorporateAction


def _parse_timestamp(ts_str: str) -> datetime:
    """Parse an ISO 8601 timestamp into a timezone‑aware datetime.

    Alpaca timestamps are returned either with a trailing ``Z`` or with an
    explicit UTC offset.  ``datetime.fromisoformat`` does not handle the
    ``Z`` suffix, so we replace it with ``+00:00``.  If the input is
    already in the correct format, it is used as is.

    Args:
        ts_str: An ISO 8601 timestamp string.

    Returns:
        A timezone‑aware :class:`datetime` instance.
    """
    if ts_str.endswith("Z"):
        ts_str = ts_str[:-1] + "+00:00"
    return datetime.fromisoformat(ts_str)


def _canonical_timeframe_map() -> Dict[str, str]:
    """Return a mapping of canonical timeframe strings to Alpaca API strings.

    Recognised canonical timeframes (keys) include ``1m``, ``15m``, ``1h``,
    and ``1d``.  Values correspond to Alpaca's expected formats like
    ``1Min`` and ``1Hour``.  The mapping is case sensitive for simplicity.
    """
    return {
        "1m": "1Min",
        "15m": "15Min",
        "1h": "1Hour",
        "1d": "1Day",
    }


class AlpacaDataProvider(DataProvider):
    """Concrete data provider using Alpaca's REST API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
    ) -> None:
        """Initialize the provider.

        Args:
            api_key: Alpaca API key ID.  Falls back to the ``ALPACA_API_KEY_ID``
                environment variable.
            secret_key: Alpaca API secret key.  Falls back to
                ``ALPACA_API_SECRET_KEY``.
            base_url: Base URL for the API.  Defaults to the value of
                ``ALPACA_BASE_URL`` or ``https://data.alpaca.markets``.
            timeout: Default request timeout in seconds.
            max_retries: Maximum number of retry attempts for rate limits and
                transient errors.
            backoff_factor: Backoff factor for exponential backoff between retries.
        """
        self.api_key = api_key or os.getenv("ALPACA_API_KEY_ID")
        self.secret_key = secret_key or os.getenv("ALPACA_API_SECRET_KEY")
        self.base_url = (
            base_url
            or os.getenv("ALPACA_BASE_URL")
            or "https://data.alpaca.markets"
        ).rstrip("/")
        self.timeout = timeout
        if not self.api_key or not self.secret_key:
            raise ValueError("Alpaca API credentials are not set")

        # Provider‑specific defaults from environment variables
        # Which data feed to use (e.g., 'iex', 'sip'). Defaults to IEX.
        self.data_feed: str = os.getenv("ALPACA_DATA_FEED", "iex")
        # How adjustments (e.g., splits) are applied to historical data. Defaults to 'raw'.
        self.adjustment: str = os.getenv("ALPACA_ADJUSTMENT", "raw")
        # Maximum number of bars returned per page. Defaults to 10000.
        try:
            self.limit: int = int(os.getenv("ALPACA_LIMIT", "10000"))
        except ValueError:
            self.limit = 10000

        # Configure a requests session with retry/backoff
        self.session = requests.Session()
        retries = Retry(
            total=max_retries,
            backoff_factor=backoff_factor,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _headers(self) -> Dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.secret_key,
        }

    def _get(self, path: str, params: Dict[str, str]) -> Dict[str, Any]:
        """Perform an HTTP GET request and return the decoded JSON.

        Args:
            path: API path relative to the base URL.
            params: Query string parameters.

        Returns:
            A dictionary parsed from the JSON response.

        Raises:
            RuntimeError: If the request fails or the response cannot be decoded.
        """
        url = f"{self.base_url}{path}"
        response = self.session.get(
            url, params=params, headers=self._headers(), timeout=self.timeout
        )
        # Authentication errors
        if response.status_code == 401 or response.status_code == 403:
            raise RuntimeError(
                f"Alpaca authentication failed: {response.status_code} {response.text}"
            )
        # After retries, raise for status on other non-success codes
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise RuntimeError(f"Alpaca API request failed: {exc}") from exc
        try:
            return response.json()
        except Exception as exc:
            raise RuntimeError(f"Failed to decode Alpaca JSON response: {exc}") from exc

    def get_historical_bars(
        self,
        symbols: Iterable[str],
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> List[Bar]:
        symbols_list = list(symbols)
        # Map our canonical timeframe to Alpaca's expected format
        tf_api = self._map_timeframe(timeframe)
        # Build base params for pagination
        params: Dict[str, str] = {
            "symbols": ",".join(symbols_list),
            "timeframe": tf_api,
            "start": self._format_datetime(start),
            "end": self._format_datetime(end),
            "limit": str(self.limit),
            "adjustment": self.adjustment,
            "feed": self.data_feed,
        }
        page_token: Optional[str] = None
        bars: List[Bar] = []
        # Iterate through pages until next_page_token is absent
        while True:
            # Only include page_token if present
            call_params = params.copy()
            if page_token:
                call_params["page_token"] = page_token
            data = self._get("/v2/stocks/bars", call_params)
            bars_json = data.get("bars", {})
            for sym, bar_list in bars_json.items():
                # Normalize the bar list: it may be a dict (single bar), None or a list
                if isinstance(bar_list, dict):
                    bar_iter = [bar_list]
                elif bar_list is None:
                    bar_iter = []
                else:
                    bar_iter = bar_list
                for entry in bar_iter:
                    # Skip entries that are not dictionaries
                    if not isinstance(entry, dict):
                        continue
                    # Parse timestamp and build Bar
                    ts = _parse_timestamp(entry.get("t"))
                    bar = Bar(
                        symbol=sym,
                        timeframe=timeframe,
                        ts=ts,
                        open=float(entry["o"]),
                        high=float(entry["h"]),
                        low=float(entry["l"]),
                        close=float(entry["c"]),
                        volume=int(entry["v"]),
                        vwap=float(entry["vw"]) if entry.get("vw") is not None else None,
                        source="alpaca",
                    )
                    bars.append(bar)
            # Determine if more pages exist
            page_token = data.get("next_page_token")
            if not page_token:
                break
        # Sort bars chronologically if needed
        bars.sort(key=lambda b: b.ts)
        return bars

    def get_latest_bars(
        self, symbols: Iterable[str], timeframe: str
    ) -> Dict[str, Bar]:
        symbols_list = list(symbols)
        tf_api = self._map_timeframe(timeframe)
        # If timeframe is one minute, call the dedicated latest endpoint
        if tf_api == "1Min":
            params = {
                "symbols": ",".join(symbols_list),
                "feed": self.data_feed,
            }
            data = self._get("/v2/stocks/bars/latest", params)
            latest: Dict[str, Bar] = {}
            bars_json = data.get("bars", {})
            for sym, entry in bars_json.items():
                ts = _parse_timestamp(entry.get("t"))
                bar = Bar(
                    symbol=sym,
                    timeframe=timeframe,
                    ts=ts,
                    open=float(entry["o"]),
                    high=float(entry["h"]),
                    low=float(entry["l"]),
                    close=float(entry["c"]),
                    volume=int(entry["v"]),
                    vwap=float(entry["vw"]) if entry.get("vw") is not None else None,
                    source="alpaca",
                )
                latest[sym] = bar
            return latest
        # Otherwise, fall back to fetching historical bars over a recent window and taking the latest
        now = datetime.now(timezone.utc)
        # Determine a reasonable lookback based on timeframe
        if tf_api.endswith("Min"):
            # For intraday granularities greater than 1 minute, look back two days
            lookback = timedelta(days=2)
        elif tf_api.endswith("Hour"):
            # For hourly bars, look back one week
            lookback = timedelta(days=7)
        else:
            # For daily bars or unknown, look back one year
            lookback = timedelta(days=365)
        start = now - lookback
        bars = self.get_historical_bars(symbols_list, timeframe, start, now)
        latest_by_sym: Dict[str, Bar] = {}
        for bar in bars:
            existing = latest_by_sym.get(bar.symbol)
            if existing is None or bar.ts > existing.ts:
                latest_by_sym[bar.symbol] = bar
        return latest_by_sym

    def get_corporate_actions(
        self, symbols: Iterable[str], start: date, end: date
    ) -> List[CorporateAction]:
        symbols_list = list(symbols)
        # Documented corporate actions endpoint (dash, not underscore)
        path = "/v1/corporate-actions"
        params = {
            "symbols": ",".join(symbols_list),
            "start": start.isoformat(),
            "end": end.isoformat(),
        }
        data = self._get(path, params)
        actions: List[CorporateAction] = []
        # The response may use different keys depending on API version
        items = (
            data.get("corporate_actions")
            or data.get("actions")
            or data.get("announcements")
            or []
        )
        for item in items:
            symbol = item.get("symbol") or item.get("ticker")
            action_type = (
                item.get("ca_type")
                or item.get("action_type")
                or item.get("type")
                or "unknown"
            )
            ex_date_str = item.get("ex_date") or item.get("exDate")
            ex_date = date.fromisoformat(ex_date_str) if ex_date_str else None
            if symbol and ex_date:
                ca = CorporateAction(
                    symbol=symbol,
                    action_type=action_type,
                    ex_date=ex_date,
                    payload_json=item,
                    source="alpaca",
                )
                actions.append(ca)
        actions.sort(key=lambda c: c.ex_date)
        return actions

    @staticmethod
    def _format_datetime(dt: datetime) -> str:
        """Format datetime for API query parameters.

        The Alpaca API expects RFC3339 timestamps in UTC with no fractional
        seconds.  If the input datetime is naive, it is assumed to be in
        UTC.  The result will always have a trailing ``Z``.

        Args:
            dt: A timezone‑aware or naive datetime.

        Returns:
            An RFC3339 timestamp string in UTC without microseconds.
        """
        # If no tzinfo, assume UTC explicitly
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        # Convert to UTC and strip microseconds
        dt_utc = dt.astimezone(timezone.utc).replace(microsecond=0)
        # Format and replace +00:00 with Z
        return dt_utc.isoformat().replace("+00:00", "Z")

    @staticmethod
    def _map_timeframe(timeframe: str) -> str:
        """Map a canonical timeframe to the Alpaca API format.

        This utility converts common shorthand notations (e.g., ``1m``) into
        Alpaca's expected strings (e.g., ``1Min``).  Unknown timeframes are
        returned unchanged to allow the API to handle or reject them.

        Args:
            timeframe: The canonical timeframe string.

        Returns:
            A string suitable for Alpaca API queries.
        """
        mapping = _canonical_timeframe_map()
        return mapping.get(timeframe, timeframe)