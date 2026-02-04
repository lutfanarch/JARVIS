"""Chart rendering utilities for Informer.

This package provides functionality to render candlestick charts with
standardized styling and overlays.  Charts are generated using
`mplfinance` and saved to PNG files.  See :mod:`informer.charts.renderer`
for details.
"""

from .renderer import (
    CHART_VERSION_DEFAULT,
    TIMEZONE_EXCHANGE,
    render_chart_for_symbol_timeframe,
)

__all__ = [
    "CHART_VERSION_DEFAULT",
    "TIMEZONE_EXCHANGE",
    "render_chart_for_symbol_timeframe",
]