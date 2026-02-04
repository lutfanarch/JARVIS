"""Proprietary trading firm evaluation status command.

This module defines a Click command group ``prop`` with a single
subcommand ``eval-status``.  The command reads the forward‑test
registry and realised outcome logs to compute evaluation progress
towards the profit target for a given prop firm profile.  It also
highlights concentration and drawdown risks relative to the profile’s
rules (e.g., the Trade The Pool 25k beginner program).

Usage::

    jarvis prop eval-status --profile trade_the_pool_25k_beginner --start 2026-01-01 --out report.json

If the ``--profile`` option is omitted the active profile is taken from
the ``PROP_PROFILE`` environment variable.  The ``--start`` option
defines the earliest New York trade date to include; when omitted the
earliest recorded run or outcome is used.  When ``--out`` is
specified the command writes a deterministic JSON report to the given
path; otherwise it prints a human‑readable summary to the console.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import click

from ..forwardtest.registry import load_registry, load_outcomes
from .profiles import get_profile, PropFirmProfile


def _parse_ny_date(date_str: str) -> date:
    """Parse a YYYY-MM-DD date string into a date object.

    This helper assumes the input is a valid ISO date.  It never raises
    and will return ``None`` on failure.
    """
    try:
        return datetime.fromisoformat(date_str).date()
    except Exception:
        return None  # type: ignore[return-value]


def _compute_drawdown(daily_pnl: Dict[str, float], account_size: float) -> Tuple[float, float]:
    """Compute the maximum drawdown in USD and as a percentage.

    Parameters
    ----------
    daily_pnl : dict[str, float]
        Mapping of NY dates to realised P&L for that date.
    account_size : float
        The account size in USD used to convert drawdown to a percentage.

    Returns
    -------
    tuple[float, float]
        The maximum drawdown in USD and as a percentage of the account
        size.  The drawdown is always non‑negative.
    """
    max_drawdown = 0.0
    if daily_pnl:
        cum_equity = 0.0
        peak = 0.0
        for date_key in sorted(daily_pnl.keys()):
            cum_equity += daily_pnl[date_key]
            if cum_equity > peak:
                peak = cum_equity
            drawdown = peak - cum_equity
            if drawdown > max_drawdown:
                max_drawdown = drawdown
    drawdown_pct = (max_drawdown / account_size) * 100.0 if account_size else 0.0
    return max_drawdown, drawdown_pct


@click.group()
def prop() -> None:
    """Proprietary trading firm utilities."""
    # The docstring above is used by Click for help output.  No body is
    # required; the group is registered by adding commands below.
    pass


@prop.command("eval-status")
@click.option(
    "--profile",
    default=None,
    help=(
        "Evaluation profile name (defaults to PROP_PROFILE environment variable). "
        "Supported profiles include trade_the_pool_25k_beginner."
    ),
)
@click.option(
    "--start",
    default=None,
    help=(
        "Earliest NY date (YYYY-MM-DD) to include in the evaluation. "
        "If omitted, the earliest recorded run or outcome date is used."
    ),
)
@click.option(
    "--out",
    "out_path",
    default=None,
    help=(
        "Optional path to write a deterministic JSON report. "
        "When provided, the report is written to this path instead of only printing to the console."
    ),
)
def eval_status_command(profile: Optional[str], start: Optional[str], out_path: Optional[str]) -> None:
    """Show evaluation progress and risk warnings for a prop firm profile.

    This command aggregates realised P&L from forward‑test outcomes and
    computes progress towards the profit target defined by the selected
    profile.  It also warns when the largest trade dominates profits
    beyond the allowed ratio, when the equity drawdown exceeds the
    maximum loss percentage or when daily losses exceed the pause
    threshold.  See the documentation for details on the computed
    metrics.
    """
    # Resolve the profile name: explicit CLI option overrides env
    profile_name = profile or os.getenv("PROP_PROFILE")
    if not profile_name:
        click.echo("[error] No profile specified and PROP_PROFILE is not set", err=True)
        return
    pf = get_profile(profile_name)
    if not pf:
        click.echo(f"[error] Unknown profile '{profile_name}'", err=True)
        return
    # Load forward test runs and outcomes
    entries = load_registry()
    outcomes = load_outcomes()
    # Determine start date: either provided or earliest date among runs/outcomes
    start_date_str: Optional[str]
    if start:
        start_date_str = start
    else:
        dates: List[str] = []
        for e in entries:
            ny_date = e.get("ny_date")
            if isinstance(ny_date, str):
                dates.append(ny_date)
        for o in outcomes:
            ny_date = o.get("ny_date")
            if isinstance(ny_date, str):
                dates.append(ny_date)
        start_date_str = min(dates) if dates else None
    # Build mapping of runs keyed by (ny_date, symbol) to the most recent entry
    runs_by_key: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for e in entries:
        if e.get("decision_status") != "TRADE":
            continue
        ny_date = e.get("ny_date")
        symbol = e.get("selected_symbol")
        if not (isinstance(ny_date, str) and isinstance(symbol, str)):
            continue
        # Apply start date filter on runs
        if start_date_str and ny_date < start_date_str:
            continue
        key = (ny_date, symbol)
        runs_by_key.setdefault(key, []).append(e)
    # Sort lists by created_at_utc so that the last element is the most recent
    for key, lst in runs_by_key.items():
        lst.sort(key=lambda r: r.get("created_at_utc", ""))
    # Prepare containers for metrics
    matched_outcomes: List[Dict[str, Any]] = []
    daily_pnl: Dict[str, float] = {}
    # Profit validity buckets (only count profitable trades)
    valid_profit_usd = 0.0
    invalid_profit_usd = 0.0
    unknown_validity_profit_usd = 0.0
    best_trade_valid_profit_usd = 0.0
    # Iterate over outcomes and match to runs
    for outcome in outcomes:
        ny_date = outcome.get("ny_date")
        symbol = outcome.get("symbol")
        # Validate date and symbol
        if not (isinstance(ny_date, str) and isinstance(symbol, str)):
            continue
        # Apply start date filter on outcomes
        if start_date_str and ny_date < start_date_str:
            continue
        key = (ny_date, symbol)
        run_list = runs_by_key.get(key)
        if not run_list:
            # Skip outcomes without a matching TRADE run
            continue
        # Use the most recent run
        run = run_list[-1]
        run_id = run.get("run_id")
        artifact_dir = run.get("artifact_dir")
        if not (isinstance(run_id, str) and isinstance(artifact_dir, str)):
            continue
        # Load the decision file to obtain entry, stop and shares
        decision_path = Path(artifact_dir) / "decision.json"
        try:
            with decision_path.open("r", encoding="utf-8") as df:
                decision_data = json.load(df)
        except Exception:
            # If the decision cannot be parsed, skip this outcome
            continue
        # Extract numeric fields
        entry_dec = None
        shares_dec = None
        if isinstance(decision_data, dict):
            entry_dec = decision_data.get("entry")
            shares_dec = decision_data.get("shares")
        try:
            entry_dec_val = float(entry_dec) if entry_dec is not None else None
            shares_val = int(shares_dec) if shares_dec is not None else None
        except Exception:
            continue
        if shares_val is None:
            continue
        # Determine realised entry: prefer outcome entry when provided
        entry_realised: Optional[float] = None
        if isinstance(outcome.get("entry"), (int, float, str)):
            try:
                entry_realised = float(outcome["entry"])
            except Exception:
                entry_realised = None
        if entry_realised is None:
            entry_realised = entry_dec_val
        if entry_realised is None:
            continue
        # Compute exit
        try:
            exit_realised = float(outcome.get("exit"))
        except Exception:
            continue
        # Compute realised pnl (exit - entry) * shares
        pnl_usd = (exit_realised - entry_realised) * shares_val
        # Update daily pnl
        daily_pnl[ny_date] = daily_pnl.get(ny_date, 0.0) + pnl_usd
        # Classify profitable trades for validity accounting
        if pnl_usd > 0:
            # Profit per share (realised profit divided by number of shares)
            try:
                profit_per_share = (exit_realised - entry_realised)
            except Exception:
                profit_per_share = None
            # Extract duration when present
            duration_val: Optional[int] = None
            dur_raw = outcome.get("duration_seconds")
            if dur_raw is not None:
                try:
                    duration_val = int(dur_raw)
                except Exception:
                    duration_val = None
            # Determine validity
            # Unknown when duration missing
            if duration_val is None:
                unknown_validity_profit_usd += pnl_usd
            else:
                if (profit_per_share is None
                    or profit_per_share < pf.min_profit_per_share_usd
                    or duration_val < pf.min_trade_duration_seconds):
                    # Invalid: either profit/share too small or duration below threshold
                    invalid_profit_usd += pnl_usd
                else:
                    # Valid
                    valid_profit_usd += pnl_usd
                    if pnl_usd > best_trade_valid_profit_usd:
                        best_trade_valid_profit_usd = pnl_usd
        # Append matched outcome record
        matched_outcomes.append({
            "ny_date": ny_date,
            "symbol": symbol,
            "run_id": run_id,
            "shares": shares_val,
            "entry_realised": entry_realised,
            "exit_realised": exit_realised,
            "pnl_usd": pnl_usd,
        })
    # Aggregate realised PnL metrics
    total_pnl_usd = sum(item["pnl_usd"] for item in matched_outcomes)
    best_trade_pnl = max((item["pnl_usd"] for item in matched_outcomes), default=0.0)
    # Compute progress towards profit target
    profit_target_usd = pf.account_size_usd * (pf.profit_target_pct / 100.0)
    progress_pct = (total_pnl_usd / profit_target_usd) * 100.0 if profit_target_usd != 0 else 0.0
    best_trade_ratio: Optional[float] = None
    if total_pnl_usd > 0.0:
        best_trade_ratio = best_trade_pnl / total_pnl_usd
    # Count positions taken: number of TRADE runs in the registry within the date range
    positions_taken = 0
    for e in entries:
        if e.get("decision_status") != "TRADE":
            continue
        ny_date = e.get("ny_date")
        if not isinstance(ny_date, str):
            continue
        if start_date_str and ny_date < start_date_str:
            continue
        positions_taken += 1
    # Outcomes logged: number of matched outcomes
    positions_count = len(matched_outcomes)
    # Compute drawdown
    max_drawdown_usd, max_drawdown_pct = _compute_drawdown(daily_pnl, pf.account_size_usd)
    # Identify daily loss violations
    daily_loss_threshold = -pf.account_size_usd * (pf.daily_pause_pct / 100.0)
    loss_violations: List[str] = []
    for d, pnl in daily_pnl.items():
        if pnl < daily_loss_threshold:
            loss_violations.append(d)
    loss_violations.sort()
    # Compute best trade ratio based on valid profits
    if valid_profit_usd > 0.0:
        best_trade_ratio_valid_profit = best_trade_valid_profit_usd / valid_profit_usd
    else:
        best_trade_ratio_valid_profit = None
    # Build warnings
    warnings: Dict[str, Any] = {
        "best_trade_concentration": False,
        "max_drawdown_breached": False,
        "daily_loss_violations": loss_violations,
        "invalid_profit_present": invalid_profit_usd > 0.0,
        "unknown_duration_present": unknown_validity_profit_usd > 0.0,
    }
    # Determine best trade concentration warning based on valid profits
    if best_trade_ratio_valid_profit is not None and best_trade_ratio_valid_profit > pf.max_position_profit_ratio:
        warnings["best_trade_concentration"] = True
    if max_drawdown_pct > pf.max_loss_pct:
        warnings["max_drawdown_breached"] = True
    # Determine end date (latest date considered)
    date_keys = [item["ny_date"] for item in matched_outcomes]
    end_date_str = max(date_keys) if date_keys else None
    # Compute days elapsed if possible
    days_elapsed: Optional[int] = None
    if start_date_str and end_date_str:
        start_dt = _parse_ny_date(start_date_str)
        end_dt = _parse_ny_date(end_date_str)
        if start_dt and end_dt:
            days_elapsed = (end_dt - start_dt).days + 1 if end_dt >= start_dt else 0
    # Assemble report dictionary
    report: Dict[str, Any] = {
        "profile_name": pf.name,
        "start_ny_date": start_date_str,
        "end_ny_date": end_date_str,
        "profit_target_usd": profit_target_usd,
        "profit_target_pct": pf.profit_target_pct,
        "realised_total_pnl_usd": total_pnl_usd,
        "progress_to_target_usd": total_pnl_usd,
        "progress_to_target_pct": progress_pct,
        "realised_best_trade_pnl_usd": best_trade_pnl,
        "best_trade_ratio": best_trade_ratio,
        "max_position_profit_ratio": pf.max_position_profit_ratio,
        # Number of TRADE runs considered in the date range
        "positions_taken": positions_taken,
        # Number of outcomes matched to those runs
        "outcomes_logged": positions_count,
        # Validity thresholds
        "min_profit_per_share_usd": pf.min_profit_per_share_usd,
        "min_trade_duration_seconds": pf.min_trade_duration_seconds,
        # Profit validity accounting
        "valid_profit_usd": valid_profit_usd,
        "invalid_profit_usd": invalid_profit_usd,
        "unknown_validity_profit_usd": unknown_validity_profit_usd,
        "best_trade_valid_profit_usd": best_trade_valid_profit_usd,
        "best_trade_ratio_valid_profit": best_trade_ratio_valid_profit,
        # Drawdown and risk metrics
        "max_drawdown_usd": max_drawdown_usd,
        "max_drawdown_pct": max_drawdown_pct,
        "max_loss_pct": pf.max_loss_pct,
        "daily_pause_pct": pf.daily_pause_pct,
        # Warnings summarising rule risk conditions
        "warnings": warnings,
        # Temporal metrics
        "days_elapsed": days_elapsed,
        "days_remaining": None,
    }
    # If output path is provided, write JSON report deterministically
    if out_path:
        out_file = Path(out_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        with out_file.open("w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, sort_keys=True)
        click.echo(f"Report written to {out_file}")
        return
    # Otherwise, print a human‑readable summary
    lines: List[str] = []
    lines.append(f"Profile: {pf.name}")
    if start_date_str:
        lines.append(f"Start NY Date: {start_date_str}")
    if end_date_str:
        lines.append(f"End NY Date: {end_date_str}")
    lines.append(f"Realised total P&L: {total_pnl_usd:.2f} USD")
    lines.append(
        f"Progress to target: {total_pnl_usd:.2f} USD ({progress_pct:.2f}% of {profit_target_usd:.2f} USD target)"
    )
    lines.append(f"Best trade P&L: {best_trade_pnl:.2f} USD")
    if best_trade_ratio is not None:
        lines.append(f"Best trade ratio (net): {best_trade_ratio:.2f}")
    else:
        lines.append("Best trade ratio (net): N/A (no realised profit)")
    lines.append(f"Max drawdown: {max_drawdown_usd:.2f} USD ({max_drawdown_pct:.2f}% of account)")
    if warnings["max_drawdown_breached"]:
        lines.append(
            f"WARNING: Drawdown {max_drawdown_pct:.2f}% exceeds max loss {pf.max_loss_pct:.2f}%"
        )
    if loss_violations:
        thresh = pf.account_size_usd * (pf.daily_pause_pct / 100.0)
        lines.append(
            f"WARNING: Daily loss exceeded pause threshold {pf.daily_pause_pct:.2f}% (–{thresh:.2f} USD) on: {', '.join(loss_violations)}"
        )
    else:
        lines.append("No daily loss violations detected.")
    # Validity accounting summary
    lines.append(f"Positions taken: {positions_taken}")
    lines.append(f"Outcomes logged: {positions_count}")
    lines.append(f"Valid profit: {valid_profit_usd:.2f} USD")
    lines.append(f"Invalid profit: {invalid_profit_usd:.2f} USD")
    lines.append(f"Unknown validity profit: {unknown_validity_profit_usd:.2f} USD")
    # Best trade ratio on valid profits
    if best_trade_ratio_valid_profit is not None:
        lines.append(f"Best valid trade ratio: {best_trade_ratio_valid_profit:.2f}")
        if warnings["best_trade_concentration"]:
            lines.append(
                f"WARNING: Best trade concentration {best_trade_ratio_valid_profit:.2f} exceeds allowed ratio {pf.max_position_profit_ratio:.2f}"
            )
    else:
        lines.append("Best valid trade ratio: N/A (no valid profit)")
    # Additional warnings for invalid/unknown profits
    if warnings.get("invalid_profit_present"):
        lines.append("WARNING: Invalid profit present in realised gains")
    if warnings.get("unknown_duration_present"):
        lines.append("WARNING: Some profitable outcomes are missing duration (unknown validity)")
    if positions_count == 0:
        lines.append("No matched trade outcomes in the specified range.")
    click.echo("\n".join(lines))