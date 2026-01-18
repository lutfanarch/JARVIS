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
from .registry import load_registry, record_run, append_outcome


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
def list_command(start: Optional[str], end: Optional[str]) -> None:
    """List recorded forward-test runs.

    The list includes the NY date, run_id, decision status and selected symbol.
    """
    entries = load_registry()
    # Filter by date range if provided
    def in_range(date_str: str) -> bool:
        if start:
            if date_str < start:
                return False
        if end:
            if date_str > end:
                return False
        return True
    filtered = [e for e in entries if in_range(e.get("ny_date", ""))]
    # Sort by ny_date then run_id
    filtered.sort(key=lambda x: (x.get("ny_date"), x.get("run_id")))
    # Print header
    click.echo("ny_date\trun_id\tstatus\tsymbol")
    for e in filtered:
        click.echo(f"{e.get('ny_date','')}\t{e.get('run_id','')}\t{e.get('decision_status','')}\t{e.get('selected_symbol') or '-'}")


@forwardtest.command("report")
@click.option("--start", required=True, type=str, help="Start NY date (YYYY-MM-DD) for the report.")
@click.option("--end", required=True, type=str, help="End NY date (YYYY-MM-DD) for the report.")
@click.option("--out", "out_path", required=True, type=str, help="Path to write the report JSON.")
def report_command(start: str, end: str, out_path: str) -> None:
    """Generate a summary report for forward-test runs.

    The report includes counts by status and symbol frequency within
    the specified date range.  Outcomes recorded via
    ``forwardtest-log-outcome`` are not yet considered in this report.
    """
    entries = load_registry()
    # Filter entries within date range
    filtered = [
        e
        for e in entries
        if start <= e.get("ny_date", "") <= end
    ]
    summary: Dict[str, Any] = {
        "total_runs": len(filtered),
        "status_counts": {},
        "symbol_counts": {},
    }
    for e in filtered:
        status = e.get("decision_status", "")
        sym = e.get("selected_symbol") or "-"
        summary["status_counts"][status] = summary["status_counts"].get(status, 0) + 1
        summary["symbol_counts"][sym] = summary["symbol_counts"].get(sym, 0) + 1
    # Write out as JSON
    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with out_file.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, sort_keys=True)
    click.echo(f"Report written to {out_file}")


@forwardtest.command("log-outcome")
@click.option("--ny-date", required=True, type=str, help="NY trade date for which to log the outcome.")
@click.option("--symbol", required=True, type=str, help="Symbol traded in the forward test.")
@click.option("--entry", required=True, type=float, help="Realised entry price.")
@click.option("--exit", required=True, type=float, help="Realised exit price.")
@click.option("--notes", type=str, default=None, help="Optional notes about the outcome.")
def log_outcome_command(ny_date: str, symbol: str, entry: float, exit: float, notes: Optional[str]) -> None:
    """Log a realised trade outcome for a forward-test run.

    The outcome is appended to a JSONL file separate from the run registry.
    """
    append_outcome(
        ny_date=ny_date,
        symbol=symbol,
        entry=entry,
        exit=exit,
        notes=notes,
    )
    click.echo(f"Logged outcome for {ny_date} {symbol}")