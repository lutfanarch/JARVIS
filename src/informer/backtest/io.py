"""I/O helpers for writing backtest artifacts.

This module centralizes writing of CSV and JSON artifacts produced by
the backtesting engine.  All functions here are deterministic: they
write data in a consistent column order and include simple version
stamps to aid downstream consumers.  Directories are created if they
do not already exist.
"""

from __future__ import annotations

import csv
import json
import os
from dataclasses import asdict
from typing import List, Dict, Any

from ..config import UNIVERSE_VERSION
from .metrics import Trade
from .strategy import BacktestConfig


def _ensure_dir(path: str) -> None:
    """Ensure that a directory exists."""
    os.makedirs(path, exist_ok=True)


def write_trades_csv(trades: List[Trade], out_path: str) -> None:
    """Write executed trades to a CSV file.

    Columns are written in a fixed order and no extra quoting is
    applied so the output remains human‑readable.  When there are no
    trades, a header row is still written to document the expected
    columns.  The header is derived from the ``Trade`` dataclass so
    that Phase 3 fields (score, vol_regime_15m, trend_regime_1h) are
    always included.
    """
    # Determine header from the Trade dataclass.  This ensures that
    # Phase 3 fields are present and preserves the defined field order.
    from dataclasses import fields

    trade_fields = [f.name for f in fields(Trade)]
    header = trade_fields
    _ensure_dir(os.path.dirname(out_path))
    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        # Write rows only if trades exist
        for tr in trades:
            writer.writerow([getattr(tr, col) for col in header])


def write_equity_curve_csv(curve: List[Dict[str, Any]], out_path: str) -> None:
    """Write the equity curve to a CSV file."""
    _ensure_dir(os.path.dirname(out_path))
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "equity"])
        writer.writeheader()
        for row in curve:
            writer.writerow(row)


def write_reasons_csv(reasons: List[Dict[str, Any]], out_path: str) -> None:
    """Write NO TRADE reasons per day to a CSV file."""
    _ensure_dir(os.path.dirname(out_path))
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "reason"])
        writer.writeheader()
        for row in reasons:
            writer.writerow(row)


def write_summary_json(
    summary: Dict[str, Any], config: BacktestConfig, out_path: str
) -> None:
    """Write backtest summary and configuration to a JSON file.

    The output includes the user‑supplied config parameters along with
    computed metrics and version information.
    """
    _ensure_dir(os.path.dirname(out_path))
    out = {
        "universe_version": UNIVERSE_VERSION,
        "config": {
            "symbols": config.symbols,
            "start_date": config.start_date.isoformat(),
            "end_date": config.end_date.isoformat(),
            "initial_cash": config.initial_cash,
            "decision_time": config.decision_time.strftime("%H:%M"),
            "decision_tz": config.decision_tz,
            "k_stop": config.k_stop,
            "k_target": config.k_target,
            "score_threshold": config.score_threshold,
            "risk_cap_pct": config.risk_cap_pct,
            "risk_cap_fixed": config.risk_cap_fixed,
            "extra_params": config.extra_params,
        },
        "metrics": summary,
    }
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)
