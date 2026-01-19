"""Command-line tools for forward test registry.

This module defines a Click command group ``forwardtest`` with
subcommands for recording runs, listing runs, generating summary reports
and logging realised trade outcomes.  All commands operate on the
JSONL-based forward-test registry defined in
``informer.forwardtest.registry``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import click

from ..config import UNIVERSE_VERSION
from ..providers.alpaca import PROVIDER_VERSION
from .registry import load_registry, record_run, append_outcome, load_outcomes


def _compute_trade_date_ny(as_of: datetime) -> str:
    """Compute the New York trade date from an aware UTC datetime.

    Args:
        as_of: A timezone-aware datetime (UTC preferred).

    Returns:
        The trade date as YYYY-MM-DD in the America/New_York timezone.
    """
    from zoneinfo import ZoneInfo
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=ZoneInfo("UTC"))
    ny_dt = as_of.astimezone(ZoneInfo("America/New_York"))
    return ny_dt.date().isoformat()


@click.group()
def forwardtest() -> None:
    """Forward-test (shadow) mode commands.

    This command group provides utilities for working with forward-test
    runs recorded in shadow mode.  Use ``forwardtest-record`` to
    persist a new run after ``decide`` completes, ``forwardtest-list``
    to view recorded runs and ``forwardtest-report`` for aggregated
    summaries.  Outcome logging is supported via
    ``forwardtest-log-outcome``.
    """
    pass


@forwardtest.command("record")
@click.option("--run-id", required=True, help="The run identifier matching the decide step.")
@click.option("--as-of", "as_of_str", required=True, help="ISO timestamp used for the decide step.")
@click.option("--mode", default=None, help="Explicit run mode (defaults to environment JARVIS_RUN_MODE or shadow).")
def record_command(run_id: str, as_of_str: str, mode: Optional[str]) -> None:
    """Record a completed forward-test run to the registry.

    This command should be invoked after the ``decide`` command in
    shadow mode has produced a decision artifact.  It assembles the
    run metadata, computes a configuration hash and writes standard
    artifacts under the forward_test directory.  A registry entry is
    appended with summary information about the run.
    """
    # Determine run_mode: CLI option overrides env, fallback to 'shadow'
    run_mode = (mode or os.getenv("JARVIS_RUN_MODE", "shadow")).lower()
    # Parse as_of datetime
    try:
        as_of = datetime.fromisoformat(as_of_str.replace("Z", "+00:00"))
    except Exception:
        click.echo(f"[error] Invalid as_of value: {as_of_str}")
        return
    # Compute New York trade date
    ny_date = _compute_trade_date_ny(as_of)
    # Determine artifact directories
    artifacts_root = Path("artifacts")
    forward_root = artifacts_root / "forward_test" / ny_date / run_id
    forward_root.mkdir(parents=True, exist_ok=True)
    # Decision file path from previous decide step
    decision_path = artifacts_root / "decisions" / f"{run_id}.json"
    # Read decision
    if not decision_path.exists():
        click.echo(f"[error] Decision file not found: {decision_path}")
        decision_data: Dict[str, Any] = {"action": "NOT_READY", "reason_codes": ["NO_DECISION_FILE"]}
        decision_schema_version: Optional[str] = None
    else:
        try:
            with decision_path.open("r", encoding="utf-8") as f:
                decision_data = json.load(f)
        except Exception:
            click.echo(f"[error] Failed to parse decision file: {decision_path}")
            decision_data = {"action": "NOT_READY", "reason_codes": ["DECISION_PARSE_ERROR"]}
        decision_schema_version = decision_data.get("decision_schema_version") if isinstance(decision_data, dict) else None
    # Determine symbols considered (from SYMBOLS env or whitelist in decision)
    syms_env = os.getenv("SYMBOLS")
    if syms_env:
        symbols = [s.strip().upper() for s in syms_env.split(",") if s.strip()]
    else:
        # fall back to whitelist from decision_data if present
        if isinstance(decision_data, dict):
            syms_from_decision = decision_data.get("whitelist", [])
        else:
            syms_from_decision = []
        symbols = list(syms_from_decision)
    # Determine selected symbol and status
    selected_symbol = decision_data.get("symbol") if isinstance(decision_data, dict) else None
    decision_status = decision_data.get("action", "NOT_READY") if isinstance(decision_data, dict) else "NOT_READY"
    reason_codes = decision_data.get("reason_codes", []) if isinstance(decision_data, dict) else []
    rationale_summary = ",".join(reason_codes) if reason_codes else None
    # Compute run_config and hash
    from hashlib import sha256
    run_config = {
        "run_id": run_id,
        "run_mode": run_mode,
        "decision_time": as_of_str,
        "decision_tz": as_of.tzinfo.tzname(None) if as_of.tzinfo else "UTC",
        "universe_version": UNIVERSE_VERSION,
        "provider_version": PROVIDER_VERSION,
        "schema_version": decision_schema_version,
    }
    config_hash = sha256(json.dumps(run_config, sort_keys=True).encode()).hexdigest()
    # Write run_config.json
    with (forward_root / "run_config.json").open("w", encoding="utf-8") as f:
        json.dump(run_config, f, indent=2, sort_keys=True)
    # Write readiness summary: simplified health/qa results; unknown by default
    readiness = {
        "health": "unknown",
        "qa": "unknown",
    }
    with (forward_root / "readiness.json").open("w", encoding="utf-8") as f:
        json.dump(readiness, f, indent=2, sort_keys=True)
    # Copy informer packet(s)
    packets_dir = artifacts_root / "packets" / run_id
    informer_packet: Dict[str, Any] = {}
    if packets_dir.exists() and packets_dir.is_dir():
        for file in packets_dir.iterdir():
            if file.suffix == ".json":
                try:
                    with file.open("r", encoding="utf-8") as fp:
                        informer_packet[file.name] = json.load(fp)
                except Exception:
                    continue
    # Write informer_packet.json
    with (forward_root / "informer_packet.json").open("w", encoding="utf-8") as f:
        json.dump(informer_packet, f, indent=2, sort_keys=True)
    # Write decision.json copy
    with (forward_root / "decision.json").open("w", encoding="utf-8") as f:
        json.dump(decision_data, f, indent=2, sort_keys=True)
    # Build validator report
    validator_report = {
        "pass": decision_status == "TRADE",
        "action": decision_status,
        "symbol": selected_symbol,
        "shares": decision_data.get("shares") if isinstance(decision_data, dict) else None,
        "risk_usd": decision_data.get("risk_usd") if isinstance(decision_data, dict) else None,
        "r_multiple": decision_data.get("r_multiple") if isinstance(decision_data, dict) else None,
        "confidence": decision_data.get("confidence") if isinstance(decision_data, dict) else None,
        "reasons": reason_codes,
        "audit": decision_data.get("audit") if isinstance(decision_data, dict) else None,
    }
    with (forward_root / "validator_report.json").open("w", encoding="utf-8") as f:
        json.dump(validator_report, f, indent=2, sort_keys=True)
    # Load trade lock to report lock status
    from ..llm.state import load_trade_lock  # local import to avoid circular import
    lock_path = Path("artifacts") / "state" / "trade_lock.json"
    lock = load_trade_lock(lock_path)
    lock_status = {
        "ny_date": ny_date,
        "lock_exists": False,
        "locked_by_run_id": None,
    }
    if lock:
        if lock.last_trade_date_ny == ny_date:
            lock_status["lock_exists"] = True
            lock_status["locked_by_run_id"] = lock.last_run_id
    with (forward_root / "lock_status.json").open("w", encoding="utf-8") as f:
        json.dump(lock_status, f, indent=2, sort_keys=True)
    # Record run in registry
    record_run(
        run_id=run_id,
        ny_date=ny_date,
        mode=run_mode,
        symbols=symbols,
        decision_status=decision_status,
        selected_symbol=selected_symbol,
        rationale_summary=rationale_summary,
        schema_version=decision_schema_version,
        config_hash=config_hash,
        artifact_dir=str(forward_root),
        lock_key=str(lock_path),
        universe_version=UNIVERSE_VERSION,
        provider_version=PROVIDER_VERSION,
    )


@forwardtest.command("list")
@click.option("--start", type=str, default=None, help="Start NY date (YYYY-MM-DD) for filtering runs.")
@click.option("--end", type=str, default=None, help="End NY date (YYYY-MM-DD) for filtering runs.")
@click.option(
    "--with-outcomes",
    is_flag=True,
    default=False,
    help="Include outcome status and realised PnL/R columns in the output.",
)
@click.option(
    "--only-missing-outcomes",
    is_flag=True,
    default=False,
    help="Show only TRADE runs where no outcome has been logged.",
)
def list_command(
    start: Optional[str],
    end: Optional[str],
    with_outcomes: bool,
    only_missing_outcomes: bool,
) -> None:
    """List recorded forward‑test runs.

    By default the list shows the New York trade date, run identifier,
    decision status and selected symbol for each recorded run.  When
    ``--with-outcomes`` is supplied, additional columns indicate
    whether a matching outcome has been logged and the realised
    performance metrics (PnL in USD and R multiple) per run.  Use
    ``--only-missing-outcomes`` to filter to TRADE runs that still
    require manual outcome logging.
    """
    entries = load_registry()
    # Filter by date range if provided
    def in_range(date_str: str) -> bool:
        if start and date_str:
            if date_str < start:
                return False
        if end and date_str:
            if date_str > end:
                return False
        return True
    filtered = [e for e in entries if in_range(e.get("ny_date", ""))]
    # Sort by ny_date then run_id for deterministic output
    filtered.sort(key=lambda x: (x.get("ny_date"), x.get("run_id")))
    # Load outcomes mapping when needed
    outcome_map = {}
    if with_outcomes or only_missing_outcomes:
        outcomes = load_outcomes()
        # Build a mapping from (ny_date, symbol) to the latest outcome record
        for o in outcomes:
            key = (o.get("ny_date"), o.get("symbol"))
            # Each successive assignment overwrites prior ones; since load_outcomes
            # returns records sorted by (ny_date, symbol, recorded_at_utc),
            # the last record for a key is the most recent.
            outcome_map[key] = o
    # Determine header
    if with_outcomes:
        header = "ny_date\trun_id\tstatus\tsymbol\toutcome_logged\tpnl_usd\tr\tentry_source"
    else:
        header = "ny_date\trun_id\tstatus\tsymbol"
    click.echo(header)
    for e in filtered:
        ny_date = e.get("ny_date", "")
        run_id = e.get("run_id", "")
        status = e.get("decision_status", "")
        symbol = e.get("selected_symbol") or "-"
        # When filtering for missing outcomes, skip non-TRADE or runs with outcomes
        if only_missing_outcomes:
            if status != "TRADE":
                continue
            # Determine if an outcome exists for this run
            outcome_rec = outcome_map.get((ny_date, symbol))
            if outcome_rec is not None:
                # If a matching outcome exists, skip
                continue
        # Base row fields
        if not with_outcomes:
            click.echo(f"{ny_date}\t{run_id}\t{status}\t{symbol}")
            continue
        # Populate outcome and metric fields
        outcome_logged: str
        pnl_str: str
        r_str: str
        entry_source: str
        if status != "TRADE":
            # Non-trade runs: no outcome expected
            outcome_logged = "-"
            pnl_str = "-"
            r_str = "-"
            entry_source = "-"
        else:
            outcome_rec = outcome_map.get((ny_date, symbol))
            if outcome_rec is None:
                # Missing outcome
                outcome_logged = "N"
                pnl_str = "-"
                r_str = "-"
                entry_source = "-"
            else:
                outcome_logged = "Y"
                # Attempt to compute realised metrics
                # Read decision file to extract entry, stop and shares
                decision_entry = None
                stop_price = None
                shares = None
                artifact_dir = e.get("artifact_dir")
                if artifact_dir:
                    decision_path = Path(artifact_dir) / "decision.json"
                    try:
                        with decision_path.open("r", encoding="utf-8") as f:
                            decision_data = json.load(f)
                        if isinstance(decision_data, dict):
                            decision_entry = decision_data.get("entry")
                            stop_price = decision_data.get("stop")
                            shares = decision_data.get("shares")
                    except Exception:
                        # Cannot load decision; treat as missing
                        pass
                # Convert numeric fields when possible
                try:
                    decision_entry_val = float(decision_entry) if decision_entry is not None else None
                except Exception:
                    decision_entry_val = None
                try:
                    stop_val = float(stop_price) if stop_price is not None else None
                except Exception:
                    stop_val = None
                try:
                    shares_val = int(shares) if shares is not None else None
                except Exception:
                    shares_val = None
                # Determine realised entry: outcome entry if present else decision entry
                realised_entry = None
                entry_src = "decision"
                if "entry" in outcome_rec and outcome_rec.get("entry") is not None:
                    try:
                        realised_entry = float(outcome_rec.get("entry"))
                        entry_src = "outcome"
                    except Exception:
                        realised_entry = None
                        entry_src = "outcome"
                if realised_entry is None:
                    realised_entry = decision_entry_val
                    entry_src = "decision"
                # Realised exit
                try:
                    exit_val = float(outcome_rec.get("exit"))
                except Exception:
                    exit_val = None
                # Compute pnl and r if all required values present
                if realised_entry is not None and shares_val is not None and exit_val is not None:
                    pnl_usd = (exit_val - realised_entry) * shares_val
                    # Compute R if stop is valid and denominator non-zero
                    r_value: Optional[float] = None
                    if stop_val is not None and (realised_entry - stop_val) != 0:
                        try:
                            r_value = (exit_val - realised_entry) / (realised_entry - stop_val)
                        except Exception:
                            r_value = None
                    pnl_str = f"{pnl_usd}"  # string representation of float
                    r_str = f"{r_value}" if r_value is not None else "-"
                    entry_source = entry_src
                else:
                    pnl_str = "-"
                    r_str = "-"
                    entry_source = entry_src if realised_entry is not None else "-"
        # Emit row with outcome columns
        click.echo(
            f"{ny_date}\t{run_id}\t{status}\t{symbol}\t{outcome_logged}\t{pnl_str}\t{r_str}\t{entry_source}"
        )


@forwardtest.command("report")
@click.option("--start", required=True, type=str, help="Start NY date (YYYY-MM-DD) for the report.")
@click.option("--end", required=True, type=str, help="End NY date (YYYY-MM-DD) for the report.")
@click.option("--out", "out_path", required=True, type=str, help="Path to write the report JSON.")
def report_command(start: str, end: str, out_path: str) -> None:
    """Generate a summary report for forward-test runs.

    The report includes counts by status and symbol frequency within
    the specified date range and aggregates outcome metrics when
    realised outcomes have been logged via ``forwardtest-log-outcome``.
    """
    entries = load_registry()
    # Filter entries within date range
    filtered_runs = [
        e
        for e in entries
        if start <= e.get("ny_date", "") <= end
    ]
    summary: Dict[str, Any] = {
        "total_runs": len(filtered_runs),
        "status_counts": {},
        "symbol_counts": {},
    }
    for e in filtered_runs:
        status = e.get("decision_status", "")
        sym = e.get("selected_symbol") or "-"
        summary["status_counts"][status] = summary["status_counts"].get(status, 0) + 1
        summary["symbol_counts"][sym] = summary["symbol_counts"].get(sym, 0) + 1

    # Load outcomes and filter by date range
    outcomes = load_outcomes()
    filtered_outcomes = [
        o for o in outcomes if start <= o.get("ny_date", "") <= end
    ]

    # Build a mapping of runs keyed by (ny_date, selected_symbol) to the latest run entry
    runs_by_key: Dict[tuple, list] = {}
    for e in entries:
        if e.get("decision_status") != "TRADE":
            continue
        ny_date = e.get("ny_date")
        symbol = e.get("selected_symbol")
        if not ny_date or not symbol:
            continue
        key = (ny_date, symbol)
        runs_by_key.setdefault(key, []).append(e)
    # Sort lists by created_at_utc to ensure the last element is the most recent
    for key, lst in runs_by_key.items():
        lst.sort(key=lambda r: r.get("created_at_utc", ""))

    outcomes_rows: list[Dict[str, Any]] = []
    matched_pnls: list[float] = []
    matched_rs: list[float] = []
    # Track daily pnl sums for max drawdown calculation
    daily_pnl: Dict[str, float] = {}

    from pathlib import Path as _Path  # avoid shadowing outer Path

    for outcome in filtered_outcomes:
        ny_date = outcome.get("ny_date")
        symbol = outcome.get("symbol")
        if not ny_date or not symbol:
            continue
        key = (ny_date, symbol)
        run_list = runs_by_key.get(key)
        if not run_list:
            # Outcome without a matching TRADE run is skipped
            continue
        # Use the most recent run (last after sorting by created_at_utc)
        run = run_list[-1]
        run_id = run.get("run_id")
        artifact_dir = run.get("artifact_dir")
        if not run_id or not artifact_dir:
            continue
        # Load the decision file
        decision_path = _Path(artifact_dir) / "decision.json"
        try:
            with decision_path.open("r", encoding="utf-8") as df:
                decision_data = json.load(df)
        except Exception:
            continue
        # Extract entry, stop, shares from decision
        decision_entry = None
        stop_price = None
        shares = None
        if isinstance(decision_data, dict):
            decision_entry = decision_data.get("entry")
            stop_price = decision_data.get("stop")
            shares = decision_data.get("shares")
        # Validate numeric types
        try:
            decision_entry_val = float(decision_entry) if decision_entry is not None else None
            stop_val = float(stop_price) if stop_price is not None else None
            shares_val = int(shares) if shares is not None else None
        except Exception:
            # Invalid numeric values
            continue
        if shares_val is None:
            continue
        # Determine realised entry: prefer outcome entry when available
        entry_source = "decision"
        outcome_entry_val: Optional[float] = None
        if "entry" in outcome and outcome.get("entry") is not None:
            try:
                outcome_entry_val = float(outcome["entry"])
            except Exception:
                outcome_entry_val = None
        if outcome_entry_val is not None:
            entry_realised = outcome_entry_val
            entry_source = "outcome"
        else:
            # fallback to decision entry
            entry_realised = decision_entry_val
        # Require a valid entry price
        if entry_realised is None:
            continue
        # Compute exit
        exit_realised = None
        try:
            exit_realised = float(outcome.get("exit"))
        except Exception:
            continue
        # Compute realised pnl and R
        pnl_usd = (exit_realised - entry_realised) * shares_val
        # Determine R: (exit - entry) / (entry - stop)
        r_value = None
        if stop_val is not None and (entry_realised - stop_val) != 0:
            try:
                r_value = (exit_realised - entry_realised) / (entry_realised - stop_val)
            except Exception:
                r_value = None
        # Append metrics
        matched_pnls.append(pnl_usd)
        if r_value is not None:
            matched_rs.append(r_value)
        # Accumulate daily pnl
        daily_pnl[ny_date] = daily_pnl.get(ny_date, 0.0) + pnl_usd
        # Build row record
        row = {
            "ny_date": ny_date,
            "symbol": symbol,
            "run_id": run_id,
            "entry_realised": entry_realised,
            "exit_realised": exit_realised,
            "shares": shares_val,
            "stop": stop_val,
            "pnl_usd": pnl_usd,
            "r": r_value,
            "notes": outcome.get("notes"),
            "entry_source": entry_source,
        }
        outcomes_rows.append(row)

    # Compute outcome summary metrics
    outcomes_total = len(matched_pnls)
    if outcomes_total > 0:
        win_count = sum(1 for p in matched_pnls if p > 0)
        win_rate = win_count / outcomes_total
        total_pnl = sum(matched_pnls)
        avg_pnl = total_pnl / outcomes_total
    else:
        win_rate = 0.0
        total_pnl = 0.0
        avg_pnl = 0.0
    # Expectancy (average R) across outcomes where R is defined
    expectancy_r = None
    if matched_rs:
        expectancy_r = sum(matched_rs) / len(matched_rs)
    # Profit factor: sum of wins / abs(sum losses), or None if no losses
    profit_factor = None
    if matched_pnls:
        sum_wins = sum(p for p in matched_pnls if p > 0)
        sum_losses = sum(p for p in matched_pnls if p < 0)
        if sum_losses < 0:
            profit_factor = sum_wins / abs(sum_losses)
        # else remain None when there are no losses
    # Max drawdown: compute equity curve from daily pnl per ny_date
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

    outcomes_summary = {
        "outcomes_total": outcomes_total,
        "outcomes_win_rate": win_rate,
        "outcomes_total_pnl_usd": total_pnl,
        "outcomes_avg_pnl_usd": avg_pnl,
        "outcomes_expectancy_r": expectancy_r,
        "outcomes_profit_factor": profit_factor,
        "outcomes_max_drawdown_usd": max_drawdown,
    }

    # Attach outcomes summary and rows to the report
    summary["outcomes_summary"] = outcomes_summary
    summary["outcomes_rows"] = outcomes_rows

    # Write out as JSON
    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with out_file.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, sort_keys=True)
    click.echo(f"Report written to {out_file}")


@forwardtest.command("log-outcome")
@click.option(
    "--run-id",
    type=str,
    default=None,
    help="Run identifier from the forward-test registry; when supplied, the NY date and symbol are derived automatically.",
)
@click.option(
    "--ny-date",
    required=False,
    type=str,
    help="NY trade date for which to log the outcome (required when --run-id is not provided).",
)
@click.option(
    "--symbol",
    required=False,
    type=str,
    help="Symbol traded in the forward test (required when --run-id is not provided).",
)
@click.option(
    "--entry",
    required=False,
    type=float,
    default=None,
    show_default=False,
    help="Realised entry price. If omitted, the entry from the decision will be used in the report.",
)
@click.option("--exit", required=True, type=float, help="Realised exit price.")
@click.option("--notes", type=str, default=None, help="Optional notes about the outcome.")
@click.option(
    "--duration-seconds",
    type=int,
    required=False,
    help="Optional trade duration in seconds.  When provided, this value is stored in the outcome record.",
)
def log_outcome_command(
    run_id: Optional[str],
    ny_date: Optional[str],
    symbol: Optional[str],
    entry: Optional[float],
    exit: float,
    notes: Optional[str],
    duration_seconds: Optional[int],
) -> None:
    """Log a realised trade outcome for a forward‑test run.

    The outcome is appended to a JSONL file separate from the run registry.
    You may specify either ``--run-id`` to derive the NY date and symbol
    automatically from the recorded run, or provide ``--ny-date`` and
    ``--symbol`` explicitly.  When both run-id and ny-date/symbol are
    supplied, the command fails with exit code 2 to avoid ambiguity.
    A realised entry price is optional; if omitted, the report will
    fall back to the decision entry when computing realised metrics.
    """
    # Validate mutually exclusive options
    import sys
    # Case: both run-id and explicit date/symbol provided
    if run_id and (ny_date or symbol):
        click.echo(
            "[error] Do not specify --ny-date or --symbol when using --run-id; supply either run-id or date/symbol, not both.",
            err=True,
        )
        raise SystemExit(2)
    # When run-id provided, derive ny_date and symbol from registry
    if run_id:
        entries = load_registry()
        # Filter entries with matching run_id
        matching = [e for e in entries if e.get("run_id") == run_id]
        if not matching:
            click.echo(f"[error] No forward-test run found with run_id {run_id}", err=True)
            raise SystemExit(2)
        # Pick the most recent entry by created_at_utc (lexicographically)
        matching.sort(key=lambda x: x.get("created_at_utc", ""))
        latest = matching[-1]
        # Ensure it's a TRADE with a selected symbol
        status = latest.get("decision_status")
        selected_symbol = latest.get("selected_symbol")
        if status != "TRADE" or not selected_symbol:
            click.echo(
                f"[error] Run {run_id} is not a TRADE or missing selected symbol (status={status})",
                err=True,
            )
            raise SystemExit(2)
        ny_date_derived = latest.get("ny_date")
        if not ny_date_derived:
            click.echo(
                f"[error] Run {run_id} has no NY date recorded in registry", err=True
            )
            raise SystemExit(2)
        ny_date = ny_date_derived
        symbol = selected_symbol
    else:
        # Without run-id, both ny-date and symbol must be provided
        if not ny_date or not symbol:
            click.echo(
                "[error] --ny-date and --symbol are required when --run-id is not provided",
                err=True,
            )
            raise SystemExit(2)
    # At this point, ny_date and symbol are populated
    # Validate that duration_seconds, when provided, is non-negative
    if duration_seconds is not None and duration_seconds < 0:
        click.echo(
            "[error] --duration-seconds must be a non-negative integer",
            err=True,
        )
        raise SystemExit(2)
    # Derive entry from decision.json when using --run-id and entry was omitted
    if run_id and entry is None:
        # Only attempt derivation if the latest registry entry has an artifact_dir
        artifact_dir = latest.get("artifact_dir") if 'latest' in locals() else None
        if isinstance(artifact_dir, str):
            dec_path = Path(artifact_dir) / "decision.json"
            try:
                with dec_path.open("r", encoding="utf-8") as df:
                    dec_data = json.load(df)
                if isinstance(dec_data, dict):
                    maybe_entry = dec_data.get("entry")
                    if isinstance(maybe_entry, (int, float)):
                        entry = float(maybe_entry)
                    elif isinstance(maybe_entry, str):
                        try:
                            entry = float(maybe_entry)
                        except Exception:
                            pass
            except Exception:
                # If any error occurs while reading/decoding the decision file,
                # leave entry unchanged (None)
                pass
    # Append the outcome record to the log
    append_outcome(
        ny_date=ny_date,  # type: ignore[arg-type]
        symbol=symbol,  # type: ignore[arg-type]
        exit=exit,
        entry=entry,
        notes=notes,
        duration_seconds=duration_seconds,
    )
    # If the realised outcome is profitable and no duration was provided, warn the user
    if entry is not None and exit > entry and duration_seconds is None:
        click.echo(
            "WARNING: profitable outcome logged without duration_seconds; "
            "specify --duration-seconds so valid profit can be evaluated",
            err=False,
        )
    # Success message
    click.echo(f"Logged outcome for {ny_date} {symbol}")