"""Tests for the Alpaca data provider.

These tests mock out HTTP requests to verify that the provider correctly
parses responses, handles different bar list formats, performs
pagination and delegates latest bar queries appropriately.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Tuple

import pytest

from informer.providers.alpaca import AlpacaDataProvider


class DummyResponse:
    """A simple stand‑in for ``requests.Response``."""

    def __init__(self, data: Dict[str, Any], status_code: int = 200) -> None:
        self._data = data
        self.status_code = status_code
        # Represent the response body as JSON for error messages
        self.text = json.dumps(data)

    def json(self) -> Dict[str, Any]:
        return self._data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


def make_provider(monkeypatch, responses: List[Tuple[str, Dict[str, Any]]]) -> AlpacaDataProvider:
    """Create an AlpacaDataProvider with mocked HTTP responses.

    Args:
        monkeypatch: pytest's monkeypatch fixture.
        responses: A list of tuples mapping request path to the JSON data
            that should be returned.  Calls are matched in order; if
            there are fewer responses than calls, the last response is reused.

    Returns:
        A provider with its ``_get`` method patched.
    """
    prov = AlpacaDataProvider(api_key="key", secret_key="secret")
    # Copy the responses so we can pop from it
    calls = list(responses)

    def fake_get(path: str, params: Dict[str, str]) -> Dict[str, Any]:
        # Pop the first matching response, or reuse the last one
        if calls:
            resp_path, data = calls.pop(0)
        else:
            resp_path, data = responses[-1]
        # Ensure path matches expectation
        assert path == resp_path
        return data

    monkeypatch.setattr(prov, "_get", fake_get)
    return prov


def test_format_datetime_no_microseconds() -> None:
    dt = datetime(2023, 1, 1, 12, 34, 56, 789000, tzinfo=timezone.utc)
    formatted = AlpacaDataProvider._format_datetime(dt)
    # Microseconds should be stripped and trailing Z present
    assert formatted == "2023-01-01T12:34:56Z"


def test_map_timeframe() -> None:
    provider = AlpacaDataProvider(api_key="x", secret_key="y")
    assert provider._map_timeframe("1m") == "1Min"
    assert provider._map_timeframe("15m") == "15Min"
    assert provider._map_timeframe("1h") == "1Hour"
    assert provider._map_timeframe("1d") == "1Day"
    # Unknown timeframe is returned unchanged
    assert provider._map_timeframe("4h") == "4h"


def test_get_historical_bars_single_dict(monkeypatch) -> None:
    # Single dict bar should be wrapped in a list
    responses = [
        (
            "/v2/stocks/bars",
            {
                "bars": {
                    "AAPL": {
                        "t": "2023-01-01T10:00:00Z",
                        "o": 1,
                        "h": 2,
                        "l": 0,
                        "c": 1.5,
                        "v": 100,
                        "vw": 1.3,
                    }
                },
                "next_page_token": None,
            },
        )
    ]
    prov = make_provider(monkeypatch, responses)
    bars = prov.get_historical_bars(["AAPL"], "1m", datetime(2023, 1, 1, tzinfo=timezone.utc), datetime(2023, 1, 2, tzinfo=timezone.utc))
    assert len(bars) == 1
    bar = bars[0]
    assert bar.symbol == "AAPL"
    assert bar.timeframe == "1m"
    assert bar.ts == datetime(2023, 1, 1, 10, 0, tzinfo=timezone.utc)


def test_get_historical_bars_pagination(monkeypatch) -> None:
    # Two pages: first has one bar and a next_page_token; second has another bar
    responses = [
        (
            "/v2/stocks/bars",
            {
                "bars": {
                    "AAPL": [
                        {
                            "t": "2023-01-01T09:00:00Z",
                            "o": 1,
                            "h": 2,
                            "l": 0,
                            "c": 1.5,
                            "v": 100,
                            "vw": 1.3,
                        }
                    ]
                },
                "next_page_token": "abc",
            },
        ),
        (
            "/v2/stocks/bars",
            {
                "bars": {
                    "AAPL": [
                        {
                            "t": "2023-01-01T10:00:00Z",
                            "o": 2,
                            "h": 3,
                            "l": 1,
                            "c": 2.5,
                            "v": 150,
                            "vw": 2.2,
                        }
                    ]
                },
                # Last page has no next_page_token
            },
        ),
    ]
    prov = make_provider(monkeypatch, responses)
    bars = prov.get_historical_bars(["AAPL"], "1m", datetime(2023, 1, 1, tzinfo=timezone.utc), datetime(2023, 1, 2, tzinfo=timezone.utc))
    # Bars should be sorted by timestamp
    assert [b.ts for b in bars] == [
        datetime(2023, 1, 1, 9, 0, tzinfo=timezone.utc),
        datetime(2023, 1, 1, 10, 0, tzinfo=timezone.utc),
    ]


def test_get_latest_bars_one_minute(monkeypatch) -> None:
    # Latest bars endpoint is used for 1m timeframe
    responses = [
        (
            "/v2/stocks/bars/latest",
            {
                "bars": {
                    "AAPL": {
                        "t": "2023-01-01T10:00:00Z",
                        "o": 1,
                        "h": 2,
                        "l": 0,
                        "c": 1.5,
                        "v": 100,
                        "vw": 1.3,
                    }
                }
            },
        )
    ]
    prov = make_provider(monkeypatch, responses)
    latest = prov.get_latest_bars(["AAPL"], "1m")
    assert isinstance(latest, dict)
    assert "AAPL" in latest
    assert latest["AAPL"].ts == datetime(2023, 1, 1, 10, 0, tzinfo=timezone.utc)


def test_get_latest_bars_fallback(monkeypatch) -> None:
    # For non‑1m timeframe, the provider should fall back to get_historical_bars
    # We'll verify this by counting the calls made to _get: one for historical bars
    calls: List[Tuple[str, Dict[str, Any]]] = [
        (
            "/v2/stocks/bars",
            {
                "bars": {
                    "AAPL": [
                        {
                            "t": "2023-01-01T10:00:00Z",
                            "o": 1,
                            "h": 2,
                            "l": 0,
                            "c": 1.5,
                            "v": 100,
                            "vw": 1.3,
                        }
                    ]
                }
            },
        )
    ]
    prov = make_provider(monkeypatch, calls)
    latest = prov.get_latest_bars(["AAPL"], "1h")
    assert "AAPL" in latest
    assert latest["AAPL"].timeframe == "1h"


def test_get_corporate_actions(monkeypatch) -> None:
    responses = [
        (
            "/v1/corporate-actions",
            {
                "announcements": [
                    {
                        "symbol": "AAPL",
                        "ca_type": "dividend",
                        "ex_date": "2023-01-15",
                        "some_other_field": 42,
                    }
                ]
            },
        )
    ]
    prov = make_provider(monkeypatch, responses)
    actions = prov.get_corporate_actions(["AAPL"], datetime(2023, 1, 1).date(), datetime(2023, 1, 31).date())
    assert len(actions) == 1
    ca = actions[0]
    assert ca.symbol == "AAPL"
    assert ca.action_type == "dividend"
    assert ca.ex_date.isoformat() == "2023-01-15"