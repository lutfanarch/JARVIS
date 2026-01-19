"""Command‑line interface for JARVIS.

This module uses the :mod:`click` library to expose user‑friendly commands
for tasks such as data ingestion, quality checks and snapshot generation.

Only the ingest command is fully implemented at this stage.  It accepts
a list of symbols, time range and timeframes, fetches OHLCV data via
the configured provider and writes it into the database.
"""

from __future__ import annotations

import os
from datetime import datetime, date, time as _time, timedelta, timezone
from pathlib import Path  # needed for charts CLI
from typing import List, Optional

import click
import json
from zoneinfo import ZoneInfo

from .db.session import get_engine
from .config import CANONICAL_WHITELIST
from .providers.alpaca import AlpacaDataProvider
from .ingestion.bars import ingest_timeframe
from .quality.checks import run_bar_quality_checks
from .quality.storage import insert_quality_events
from .features.indicators import compute_indicators
from .features.patterns import compute_patterns  # new import
from .features.regimes import compute_regimes  # import regimes
from .features.storage import upsert_features_snapshot
from .charts.renderer import render_chart_for_symbol_timeframe, CHART_VERSION_DEFAULT
from .packets.builder import build_informer_packet
from .packets.models import SCHEMA_VERSION_DEFAULT
from .health.checks import build_health_report
from .providers.alpaca import PROVIDER_VERSION

# LLM imports for decide command
from .llm.pipeline import load_packets, run_decision_pipeline
from .llm.client import FakeLLMClient, LLMClient
from .llm.models import FinalDecision
# Import the telegram module for notifications.  The actual function is accessed via
# ``telegram.send_message`` to allow tests to monkeypatch the module attribute
from .notify import telegram

# Backtest imports
from .backtest.strategy import BacktestConfig
from .backtest.engine import BacktestEngine
from .backtest.io import (
    write_trades_csv,
    write_equity_curve_csv,
    write_reasons_csv,
    write_summary_json,
)
from .backtest.costs import CostModel

# Phase 3 validation helpers
from .backtest.validation import run_parameter_sweep, run_walkforward
from .config import UNIVERSE_VERSION

# Forward test CLI group import
from .forwardtest.cli import forwardtest
# Import the prop CLI group for prop‑firm evaluation status.  The
# group is registered near the bottom of this module via
# cli.add_command.
from .props.cli import prop

import subprocess
import sys
from datetime import datetime, timezone
import json as _json
import shutil

@click.command(name="verify-dod-b", help="Verify the Definition of Done (DoD‑B) checklist and write a report.")
@click.option(
    "--run-id",
    default="shipit",
    help="Identifier used for naming the verification report (defaults to 'shipit').",
)
@click.option(
    "--skip-pytest",
    is_flag=True,
    default=False,
    help="Skip running the pytest suite during verification.",
)
def verify_dod_b(run_id: str, skip_pytest: bool) -> None:
    """Run the Definition of Done (DoD‑B) checklist and write a report.

    This command performs a series of deterministic checks to verify that
    the repository is ready for final delivery.  It always writes a
    JSON report under ``artifacts/shipit/<run-id>.json`` detailing the
    results of each step along with any captured output.  If any
    required step fails the command exits with status 2; otherwise it
    exits with status 0.  The optional ``--skip-pytest`` flag can be
    used to bypass the test suite for faster iterations.

    Parameters
    ----------
    run_id : str
        Unique identifier appended to generated artifact names.
    skip_pytest : bool
        When True, the pytest step is skipped and marked as passed.
    """
    # Record the start time in UTC ISO format
    started_at = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
    steps: list[dict[str, object]] = []
    overall_pass = True
    # Helper to run a subprocess step and collect output
    def _run_subprocess(cmd: list[str], env: dict[str, str]) -> tuple[int, str, str]:
        """Run a subprocess capturing stdout and stderr.

        Returns a tuple of (exit_code, stdout_tail, stderr_tail).  The
        output tails are truncated to the last 20 lines to keep the
        report manageable and deterministic.
        """
        try:
            completed = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=300,
            )
            stdout_lines = completed.stdout.splitlines()
            stderr_lines = completed.stderr.splitlines()
            stdout_tail = "\n".join(stdout_lines[-20:])
            stderr_tail = "\n".join(stderr_lines[-20:])
            return completed.returncode, stdout_tail, stderr_tail
        except Exception as exc:
            # If the subprocess cannot be executed, capture the exception
            return 1, "", f"Exception: {exc}"

    # Step A: repository sanity
    # Determine whether the current working directory appears to be a valid repository.
    repo_ok = Path("pyproject.toml").exists() and Path("src").is_dir() and Path("tests").is_dir()
    # Always record the repo_sanity step in the report.  It passes only if all
    # required files/directories exist in the current working directory.
    steps.append(
        {
            "name": "repo_sanity",
            "pass": bool(repo_ok),
            "exit_code": 0 if repo_ok else 1,
            "notes": "Verify pyproject.toml, src/ and tests/ exist in current directory",
            "stdout_tail": "",
            "stderr_tail": "",
        }
    )
    # If the repository sanity check fails, immediately write the report and exit.
    if not repo_ok:
        overall_pass = False
        # Record finish time for the report
        finished_at = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
        report = {
            "overall_pass": bool(overall_pass),
            "started_at_utc": started_at,
            "finished_at_utc": finished_at,
            "steps": steps,
        }
        # Ensure the shipit directory exists
        shipit_dir = Path("artifacts") / "shipit"
        shipit_dir.mkdir(parents=True, exist_ok=True)
        report_path = shipit_dir / f"{run_id}.json"
        try:
            with report_path.open("w", encoding="utf-8") as f:
                _json.dump(report, f, indent=2, sort_keys=True)
        except Exception:
            # Print an error if report writing fails; continue to exit
            click.echo(f"[error] Failed to write report to {report_path}", err=True)
        # Exit immediately with status 2 indicating failure.  No further
        # verification steps are executed when the repository is missing.
        sys.exit(2)

    # Prepare base environment: ensure PYTHONPATH points to the src directory
    base_env = os.environ.copy()
    # Always prepend/replace PYTHONPATH with a relative src path for deterministic import resolution
    base_env["PYTHONPATH"] = "src"

    # Step B: run test suite unless skipped
    if skip_pytest:
        steps.append(
            {
                "name": "pytest",
                "pass": True,
                "exit_code": 0,
                "notes": "Skipped pytest via --skip-pytest",
                "stdout_tail": "",
                "stderr_tail": "",
            }
        )
    else:
        exit_code, stdout_tail, stderr_tail = _run_subprocess(
            [sys.executable, "-m", "pytest", "-q"], base_env
        )
        passed = exit_code == 0
        steps.append(
            {
                "name": "pytest",
                "pass": bool(passed),
                "exit_code": exit_code,
                "notes": "Run test suite via pytest -q",
                "stdout_tail": stdout_tail,
                "stderr_tail": stderr_tail,
            }
        )
        if not passed:
            overall_pass = False

    # Step C: CLI smoke steps
    # Helper to run a CLI command and determine pass/fail based on exit code expectations
    def _cli_step(
        step_name: str,
        args: list[str],
        env_overrides: dict[str, str] | None = None,
        expect_zero: bool = True,
        extra_check: callable | None = None,
    ) -> None:
        nonlocal overall_pass
        env = base_env.copy()
        if env_overrides:
            env.update(env_overrides)
        # Invoke the CLI via ``python -m informer`` rather than ``informer.cli``.
        cmd = [sys.executable, "-m", "informer"] + args
        exit_code, stdout_tail, stderr_tail = _run_subprocess(cmd, env)
        # Determine pass according to expectation
        step_pass = (exit_code == 0) if expect_zero else (exit_code != 0)
        notes = ""
        # If there is an extra validation callable, invoke it and combine its result
        if extra_check is not None:
            try:
                extra_ok = extra_check()
            except Exception:
                extra_ok = False
            if not extra_ok:
                step_pass = False
        steps.append(
            {
                "name": step_name,
                "pass": bool(step_pass),
                "exit_code": exit_code,
                "notes": notes,
                "stdout_tail": stdout_tail,
                "stderr_tail": stderr_tail,
            }
        )
        if not step_pass:
            overall_pass = False

    # CLI: jarvis --help
    _cli_step("cli_help", ["--help"], expect_zero=True)
    # jarvis db-init with DATABASE_URL set
    _cli_step(
        "db_init",
        ["db-init"],
        env_overrides={"DATABASE_URL": "sqlite:///./jarvis.db"},
        expect_zero=True,
    )
    # jarvis smoke-test
    _cli_step(
        "smoke_test",
        ["smoke-test", "--db-path", "./jarvis_smoke.db", "--run-id", f"{run_id}_smoke", "--keep"],
        expect_zero=True,
    )
    # jarvis config-check shadow: should succeed
    _cli_step(
        "config_check_shadow",
        ["config-check", "--mode", "shadow"],
        expect_zero=True,
    )
    # jarvis config-check live: should return non-zero (missing env vars)
    _cli_step(
        "config_check_live",
        ["config-check", "--mode", "live"],
        expect_zero=False,
    )
    # Prepare for daily-scan: remove artifacts directory to ensure a fresh run
    try:
        shutil.rmtree("artifacts")
    except Exception:
        pass
    # Extra check after daily-scan: verify artifact files exist and registry contains run-id
    def _daily_scan_extra_check() -> bool:
        decisions_path = Path("artifacts") / "decisions" / f"{run_id}_daily.json"
        runs_path = Path("artifacts") / "runs" / f"{run_id}_daily.json"
        fwd_path = Path("artifacts") / "forward_test" / "forward_test_runs.jsonl"
        if not (decisions_path.exists() and runs_path.exists() and fwd_path.exists()):
            return False
        # Verify run-id appears in the forward-test runs file
        try:
            content = fwd_path.read_text(encoding="utf-8")
            return f"{run_id}_daily" in content
        except Exception:
            return False

    _cli_step(
        "daily_scan",
        [
            "daily-scan",
            "--run-id",
            f"{run_id}_daily",
            "--run-mode",
            "shadow",
        ],
        env_overrides={"DATABASE_URL": "sqlite:///./jarvis.db"},
        expect_zero=True,
        extra_check=_daily_scan_extra_check,
    )
    # Record finish time and write report
    finished_at = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
    report = {
        "overall_pass": bool(overall_pass),
        "started_at_utc": started_at,
        "finished_at_utc": finished_at,
        "steps": steps,
    }
    # Ensure report directory exists
    shipit_dir = Path("artifacts") / "shipit"
    shipit_dir.mkdir(parents=True, exist_ok=True)
    report_path = shipit_dir / f"{run_id}.json"
    try:
        with report_path.open("w", encoding="utf-8") as f:
            _json.dump(report, f, indent=2, sort_keys=True)
    except Exception:
        # If writing the report fails, attempt to print to stderr
        click.echo(f"[error] Failed to write report to {report_path}", err=True)
    # Exit with 0 if all passed, else 2
    if overall_pass:
        sys.exit(0)
    else:
        sys.exit(2)

# -----------------------------------------------------------------------------

# CLI group definition
#
# Define the Click group before any use of @cli.command.  If this group is
# defined later in the module, decorators referencing ``cli`` will raise
# NameError at import time.  There must be exactly one ``cli`` definition.

@click.group()
def cli() -> None:
    """JARVIS command-line interface."""
    pass



def _previous_weekday(d: date) -> date:
    """Return the previous weekday (Mon-Fri) before the given date.

    If the input date is a Monday, this returns the preceding Friday.
    This helper is used to determine the warmup start date for
    indicator initialization so that weekend days are skipped.
    """
    from datetime import timedelta
    prev_day = d - timedelta(days=1)
    while prev_day.weekday() >= 5:
        prev_day = prev_day - timedelta(days=1)
    return prev_day


def _compute_warmup_start_date(start_date: date, timeframe: str = "15m") -> date:
    """Compute the earliest trading day to load for warmup history.

    Backtests rely on a minimum number of bars (``required_warmup_bars``)
    before a trade may be taken.  For intraday 15‑minute bars during
    Regular Trading Hours there are 26 bars per trading day.  To ensure
    that the warmup threshold is satisfied even when the first trading
    day in the requested range has only a handful of bars before the
    decision time, this helper adds a small safety margin to the
    computed lookback.  The number of trading days to load is given by

    ``ceil(required_warmup_bars / 26) + 2``

    The ``+2`` accounts for days with partial data (e.g., when the
    decision time is early in the session) and ensures determinism across
    backtests.  The helper then walks backwards from the user‑specified
    start date, counting only weekdays (Monday through Friday), until
    the required number of prior trading days have been collected.  The
    resulting date is returned.  If the database does not contain
    enough history before this date, warmup gating will still block
    trading on the first days of the backtest and record an appropriate
    reason.

    Parameters
    ----------
    start_date : date
        The first requested trading day for the backtest (the day the
        user wants metrics/trades to begin).
    timeframe : str, optional
        The bar timeframe.  Only "15m" is currently used for intraday
        warmup.  Other timeframes will fall back to the same default
        warmup bars count.

    Returns
    -------
    date
        A date preceding ``start_date`` by a deterministic number of
        trading days.  Bars from this date onward should be loaded to
        satisfy indicator warmup; bars prior to this date are not
        required.
    """
    # Import here to avoid circular dependency at module load time
    from .backtest.splits import required_warmup_bars
    warmup_bars = required_warmup_bars(timeframe)
    # For 15m bars during Regular Trading Hours there are 26 bars per day
    bars_per_day = 26
    # Determine how many trading days of history are needed.  Use a
    # ceiling division for warmup bars and add a safety buffer of two
    # additional trading days to account for partial sessions.  This
    # ensures that even on a day with few bars before the decision time
    # there will still be >= required_warmup_bars bars available.
    from math import ceil
    trading_days_needed = ceil(warmup_bars / bars_per_day) + 2
    # Walk backwards across calendar days, counting only weekdays
    from datetime import timedelta
    d = start_date
    count = 0
    while count < trading_days_needed:
        d = d - timedelta(days=1)
        # Monday=0, Sunday=6; only count weekdays as trading days
        if d.weekday() < 5:
            count += 1
    return d


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO date or datetime string into an aware datetime.

    Accepted formats include ``YYYY-MM-DD``, ``YYYY-MM-DDTHH:MM:SS``,
    and timezone‑aware variants.  A trailing ``Z`` will be treated as
    ``+00:00``.  If no timezone information is present, UTC is assumed.
    """
    if value is None:
        return None
    v = value.strip()
    # If date only, interpret as midnight UTC
    if len(v) == 10 and v[4] == '-' and v[7] == '-':
        dt = datetime.fromisoformat(v)
        return dt.replace(tzinfo=timezone.utc)
    # Replace trailing Z with +00:00
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt



def _default_whitelist() -> List[str]:
    """Return the default symbol whitelist.

    The environment variable ``SYMBOLS`` overrides the hard‑coded
    fallback.  The list is split on commas and stripped of whitespace.

    If SYMBOLS contains any symbol not in the canonical whitelist, a UsageError
    is raised.
    """
    env_syms = os.getenv("SYMBOLS")
    if env_syms:
        parsed = [s.strip().upper() for s in env_syms.split(",") if s.strip()]
        extras = [s for s in parsed if s not in CANONICAL_WHITELIST]
        if extras:
            raise click.UsageError(
                f"Environment variable SYMBOLS contains invalid symbols {extras}; allowed values are a subset of {CANONICAL_WHITELIST}"
            )
        return list(parsed)
    return list(CANONICAL_WHITELIST)

def _default_timeframes() -> List[str]:
    """Return the default timeframes list from the environment or fallback."""
    env_tf = os.getenv("TIMEFRAMES")
    if env_tf:
        return [s.strip() for s in env_tf.split(",") if s.strip()]
    return ["15m", "1h", "1d"]


# -----------------------------------------------------------------------------
# Database initialization command
#
# JARVIS operates on a relational database whose schema is managed by Alembic
# migrations.  When running locally, especially on Windows, a lightweight
# SQLite database can be used in place of TimescaleDB/Postgres.  The
# `db-init` CLI command runs all pending Alembic migrations against the
# configured database URL (taken from the ``DATABASE_URL`` environment
# variable) to create the required tables.  For SQLite, TimescaleDB‑specific
# statements are skipped transparently in the migration script.  This
# command prints a success message upon completion or raises a Click
# exception on failure.
@cli.command(name="db-init")
def db_init() -> None:
    """Initialize the database schema using Alembic migrations.

    This command applies all pending Alembic migrations to bring the
    database to the latest schema version.  It reads the database
    connection string from the ``DATABASE_URL`` environment variable
    and uses a helper from :mod:`informer.db.migrations` to run
    ``upgrade head``.  On success a confirmation message is printed;
    on failure a ClickException is raised.
    """
    from .db.migrations import upgrade_head  # imported here to avoid circular deps
    try:
        upgrade_head()
    except Exception as exc:
        raise click.ClickException(f"Database initialization failed: {exc}")
    click.echo("Database initialization complete.")


# -----------------------------------------------------------------------------
# Configuration check command
#
# This command validates that the environment is correctly configured for
# running JARVIS in either shadow or live mode.  In shadow mode the
# command simply verifies that the CLI can load and operate without any
# API keys and always exits successfully.  In live mode the command checks
# that all required API keys are present in the environment.  If any keys
# are missing it reports them and exits with a non‑zero status.  The keys
# currently required for live mode are:
#   - ALPACA_API_KEY_ID
#   - ALPACA_API_SECRET_KEY
#   - OPENAI_API_KEY
#   - GEMINI_API_KEY or GOOGLE_API_KEY (either key satisfies the Gemini provider)
#
# The command does not perform any network calls and is deterministic and
# CWD‑independent.  Missing variable names are reported in sorted order to
# ensure consistent output.
@cli.command(name="config-check")
@click.option(
    "--mode",
    default="shadow",
    type=click.Choice(["shadow", "live"], case_sensitive=False),
    help="Configuration mode to validate: 'shadow' or 'live'",
)
def config_check(mode: str) -> None:
    """Validate that required configuration variables are present.

    In shadow mode this command always succeeds regardless of which
    environment variables are set.  In live mode it checks for the
    presence of the API keys required to operate against live data
    providers and LLM backends.  Missing keys are listed and the
    command exits with status 2.
    """
    mode_normalised = (mode or "shadow").lower()
    # In shadow mode no keys are required; exit successfully
    if mode_normalised == "shadow":
        click.echo("OK (shadow); live keys not required")
        return
    # For live mode determine which environment variables are missing
    missing: list[str] = []
    # Required Alpaca keys
    if not os.getenv("ALPACA_API_KEY_ID"):
        missing.append("ALPACA_API_KEY_ID")
    if not os.getenv("ALPACA_API_SECRET_KEY"):
        missing.append("ALPACA_API_SECRET_KEY")
    # Required OpenAI key
    if not os.getenv("OPENAI_API_KEY"):
        missing.append("OPENAI_API_KEY")
    # Gemini can be satisfied by GEMINI_API_KEY or GOOGLE_API_KEY; require at least one
    if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
        # Report missing Gemini provider key deterministically using the GEMINI_API_KEY name
        missing.append("GEMINI_API_KEY")
    # Sort missing names for deterministic output
    missing.sort()
    if missing:
        # Report missing variables to stderr and exit with code 2
        click.echo("Missing environment variables: " + ", ".join(missing), err=True)
        # Use click context to exit with explicit code (2 for consistency with Click usage errors)
        ctx = click.get_current_context()
        ctx.exit(2)
    else:
        click.echo("OK (live)")


def _build_provider() -> AlpacaDataProvider:
    """Construct the default data provider for the CLI.

    Currently this returns an :class:`AlpacaDataProvider` instance.  The
    provider may read API keys and configuration from environment
    variables.  This function is factored out so tests can monkeypatch
    it easily.
    """
    return AlpacaDataProvider()


def _build_engine() -> "sqlalchemy.engine.Engine":
    """Construct a SQLAlchemy engine based on the DATABASE_URL env var."""
    return get_engine()




@cli.command()
@click.option(
    "--symbols",
    type=str,
    help="Comma‑separated list of symbols to ingest. Must be a subset of the configured whitelist.",
)
@click.option(
    "--timeframes",
    type=str,
    default=None,
    help="Comma‑separated list of timeframes to ingest (default from TIMEFRAMES env var).",
)
@click.option(
    "--start",
    type=str,
    default=None,
    help="Start date or datetime in ISO format (default uses incremental ingestion)",
)
@click.option(
    "--end",
    type=str,
    default=None,
    help="End date or datetime in ISO format (default: now in UTC)",
)
def ingest(symbols: Optional[str], timeframes: Optional[str], start: Optional[str], end: Optional[str]) -> None:
    """Fetch OHLCV bars from the provider and write them into the database.

    Examples::

        python -m informer ingest --symbols AAPL,MSFT --start 2025-01-01 --end 2025-01-10

    The list of symbols must be a subset of the whitelist defined by the
    ``SYMBOLS`` environment variable or the default built‑in list.  If
    ``--start`` is omitted, incremental ingestion will begin from the
    last available bar for each symbol/timeframe minus a small overlap
    window.  If there is no data in the database, a safe lookback
    period is used: 14 days for 15m, 60 days for 1h and 400 days for 1d.
    """
    whitelist = _default_whitelist()
    # Determine requested symbols: use CLI value if provided, else env SYMBOLS
    if symbols:
        requested = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        requested = whitelist
    # Enforce whitelist subset
    extra = [s for s in requested if s not in whitelist]
    if extra:
        raise click.UsageError(
            f"Requested symbols {extra} are not in the allowed whitelist {whitelist}"
        )
    # Determine timeframes list
    if timeframes:
        tf_list = [tf.strip() for tf in timeframes.split(",") if tf.strip()]
    else:
        tf_list = _default_timeframes()
    # Parse start and end datetimes once; start may be None
    end_dt = _parse_datetime(end) or datetime.now(timezone.utc)
    # Build provider and engine
    provider = _build_provider()
    engine = _build_engine()
    # For each timeframe, determine start per incremental ingestion if needed
    for tf in tf_list:
        # Convert canonical timeframe to Pandas/Alpaca durations for overlap
        # Define safe lookback periods and overlap windows per timeframe
        tf_lower = tf.lower()
        if start:
            # Use explicit start from CLI, parse once outside loop
            start_dt = _parse_datetime(start)
            if start_dt is None:
                raise click.UsageError(f"Could not parse start value '{start}'")
        else:
            # Compute incremental start.  We need to determine the latest bar
            # timestamp for each requested symbol individually.  The start
            # time will be the earliest of these per‑symbol start points.
            from sqlalchemy import select, func  # local import to avoid circular
            from informer.ingestion.bars import bars_table  # absolute import to avoid ImportError
            # Query max(ts) per symbol for the given timeframe
            with engine.connect() as conn:
                rows = conn.execute(
                    select(
                        bars_table.c.symbol,
                        func.max(bars_table.c.ts),
                    )
                    .where(
                        bars_table.c.timeframe == tf_lower,
                        bars_table.c.symbol.in_(requested),
                    )
                    .group_by(bars_table.c.symbol)
                ).fetchall()
            # Map symbol to its max timestamp
            max_by_symbol = {row[0]: row[1] for row in rows}
            start_candidates = []
            for sym in requested:
                max_ts = max_by_symbol.get(sym)
                if max_ts is not None:
                    # Ensure timestamp is timezone‑aware; assume UTC if naive
                    if getattr(max_ts, "tzinfo", None) is None:
                        max_ts = max_ts.replace(tzinfo=timezone.utc)
                    # Calculate overlap window: 5 bars worth
                    if tf_lower.endswith("m"):
                        try:
                            minutes = int(tf_lower[:-1])
                        except Exception:
                            minutes = 15
                        delta = timedelta(minutes=minutes * 5)
                    elif tf_lower.endswith("h"):
                        try:
                            hours = int(tf_lower[:-1])
                        except Exception:
                            hours = 1
                        delta = timedelta(hours=hours * 5)
                    else:
                        delta = timedelta(days=5)
                    start_sym = max_ts - delta
                else:
                    # No data: safe lookback based on timeframe
                    if tf_lower == "15m":
                        lookback = timedelta(days=14)
                    elif tf_lower == "1h":
                        lookback = timedelta(days=60)
                    else:
                        lookback = timedelta(days=400)
                    start_sym = end_dt - lookback
                start_candidates.append(start_sym)
            # Choose the earliest start across symbols
            start_dt = min(start_candidates)
        # Ensure start <= end
        # Ensure start <= end
        if start_dt > end_dt:
            click.echo(f"[warning] start {start_dt} is after end {end_dt}; skipping timeframe {tf}")
            continue
        # Normalize start and end to UTC (already done by _parse_datetime).  provider expects tz-aware datetimes.
        stats = ingest_timeframe(
            provider=provider,
            engine=engine,
            symbols=requested,
            timeframe=tf_lower,
            start=start_dt,
            end=end_dt,
        )
        # Print summary for this timeframe
        click.echo(
            f"Ingested {stats.bars_fetched} bars ({stats.bars_upserted} upserted) for timeframe {tf} between "
            f"{stats.start.isoformat()} and {stats.end.isoformat()}"
        )


@cli.command()
@click.option(
    "--symbols",
    type=str,
    help="Comma‑separated list of symbols to run QA on. Must be a subset of the configured whitelist.",
)
@click.option(
    "--timeframes",
    type=str,
    default=None,
    help="Comma‑separated list of timeframes to evaluate (default from TIMEFRAMES env var).",
)
@click.option(
    "--start",
    type=str,
    default=None,
    help="Start date or datetime in ISO format (default uses timeframe-based lookback)",
)
@click.option(
    "--end",
    type=str,
    default=None,
    help="End date or datetime in ISO format (default: now in UTC)",
)
@click.option(
    "--run-id",
    "run_id_opt",
    type=str,
    default=None,
    help="Optional run identifier for this QA run (default: env RUN_ID or generated)",
)
def qa(
    symbols: Optional[str],
    timeframes: Optional[str],
    start: Optional[str],
    end: Optional[str],
    run_id_opt: Optional[str],
) -> None:
    """Evaluate data quality on stored bars and log issues.

    This command runs a series of deterministic checks on stored bar data
    for each requested (symbol, timeframe).  Results are written to
    the ``data_quality_events`` table.  See ``python -m informer qa --help``
    for usage.
    """
    whitelist = _default_whitelist()
    # Determine requested symbols: use CLI value if provided, else env SYMBOLS
    if symbols:
        requested = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        requested = whitelist
    # Enforce whitelist subset
    extra = [s for s in requested if s not in whitelist]
    if extra:
        raise click.UsageError(
            f"Requested symbols {extra} are not in the allowed whitelist {whitelist}"
        )
    # Determine timeframes list
    if timeframes:
        tf_list = [tf.strip() for tf in timeframes.split(",") if tf.strip()]
    else:
        tf_list = _default_timeframes()
    # Parse end datetime
    end_dt = _parse_datetime(end) or datetime.now(timezone.utc)
    # Determine run_id
    run_id = run_id_opt or os.getenv("RUN_ID")
    if not run_id:
        # Generate timestamp run id (UTC)
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # Build engine
    engine = _build_engine()
    total_errors = 0
    total_warns = 0
    total_events = 0
    for tf in tf_list:
        tf_lower = tf.lower()
        # Determine start per timeframe
        if start:
            start_dt = _parse_datetime(start)
            if start_dt is None:
                raise click.UsageError(f"Could not parse start value '{start}'")
        else:
            # Use timeframe-based lookback
            if tf_lower == "15m":
                lookback = timedelta(days=14)
            elif tf_lower == "1h":
                lookback = timedelta(days=60)
            else:
                lookback = timedelta(days=400)
            start_dt = end_dt - lookback
        # For each symbol in requested list, fetch bars and run checks
        from sqlalchemy import select
        from informer.ingestion.bars import bars_table  # import here to avoid circular
        for sym in requested:
            # Query bars for this symbol/timeframe and range
            with engine.connect() as conn:
                rows = conn.execute(
                    select(bars_table).where(
                        bars_table.c.symbol == sym,
                        bars_table.c.timeframe == tf_lower,
                        bars_table.c.ts >= start_dt,
                        bars_table.c.ts < end_dt,
                    ).order_by(bars_table.c.ts)
                ).fetchall()
            passed, events = run_bar_quality_checks(
                symbol=sym,
                timeframe=tf_lower,
                bars=rows,
                start=start_dt,
                end=end_dt,
                run_id=run_id,
            )
            # Insert events into DB
            insert_quality_events(engine, events)
            # Count events by severity
            err_count = sum(1 for ev in events if ev.severity == "ERROR")
            warn_count = sum(1 for ev in events if ev.severity == "WARN")
            total_errors += err_count
            total_warns += warn_count
            total_events += len(events)
            status = "PASS" if err_count == 0 else "FAIL"
            click.echo(
                f"{sym} {tf}: {status} ({err_count} errors, {warn_count} warnings)"
            )
    click.echo(
        f"Total events: {total_events} ({total_errors} errors, {total_warns} warnings)"
    )


@cli.command()
@click.option(
    "--symbols",
    type=str,
    help="Comma‑separated list of symbols to compute features for. Must be a subset of the configured whitelist.",
)
@click.option(
    "--timeframes",
    type=str,
    default=None,
    help="Comma‑separated list of timeframes to compute indicators on (default from TIMEFRAMES env var).",
)
@click.option(
    "--start",
    type=str,
    default=None,
    help="Start date or datetime in ISO format (default uses timeframe-based lookback)",
)
@click.option(
    "--end",
    type=str,
    default=None,
    help="End date or datetime in ISO format (default: now in UTC)",
)
@click.option(
    "--feature-version",
    "feature_version_opt",
    type=str,
    default=None,
    help="Feature version identifier (default: env FEATURE_VERSION or 'v0.1')",
)
def features(
    symbols: Optional[str],
    timeframes: Optional[str],
    start: Optional[str],
    end: Optional[str],
    feature_version_opt: Optional[str],
) -> None:
    """Compute technical indicators and store feature snapshots.

    This command computes EMA, RSI, ATR and VWAP indicators for the
    specified symbols and timeframes over a given date range.  Results
    are persisted to the ``features_snapshot`` table with an associated
    feature version.
    """
    whitelist = _default_whitelist()
    if symbols:
        requested = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        requested = whitelist
    extra = [s for s in requested if s not in whitelist]
    if extra:
        raise click.UsageError(
            f"Requested symbols {extra} are not in the allowed whitelist {whitelist}"
        )
    if timeframes:
        tf_list = [tf.strip() for tf in timeframes.split(",") if tf.strip()]
    else:
        tf_list = _default_timeframes()
    end_dt = _parse_datetime(end) or datetime.now(timezone.utc)
    # Determine feature version
    feature_version = feature_version_opt or os.getenv("FEATURE_VERSION") or "v0.1"
    engine = _build_engine()
    from sqlalchemy import select
    from informer.ingestion.bars import bars_table  # import here to avoid circular
    for tf in tf_list:
        tf_lower = tf.lower()
        # Determine start and fetch_start per timeframe
        if start:
            start_dt = _parse_datetime(start)
            if start_dt is None:
                raise click.UsageError(f"Could not parse start value '{start}'")
        else:
            # Lookback periods per timeframe
            if tf_lower == "15m":
                lookback = timedelta(days=14)
            elif tf_lower == "1h":
                lookback = timedelta(days=60)
            else:
                lookback = timedelta(days=400)
            start_dt = end_dt - lookback
        # Compute warmup buffer: ~250 bars worth
        if tf_lower.endswith("m"):
            # minutes timeframe (e.g., 15m)
            try:
                minutes = int(tf_lower[:-1])
            except Exception:
                minutes = 15
            buffer = timedelta(minutes=minutes * 250)
        elif tf_lower.endswith("h"):
            try:
                hours = int(tf_lower[:-1])
            except Exception:
                hours = 1
            buffer = timedelta(hours=hours * 250)
        else:
            # daily timeframe
            buffer = timedelta(days=250)
        fetch_start = start_dt - buffer
        for sym in requested:
            # Query bars from DB for this symbol/timeframe including warmup
            with engine.connect() as conn:
                rows = conn.execute(
                    select(bars_table).where(
                        bars_table.c.symbol == sym,
                        bars_table.c.timeframe == tf_lower,
                        bars_table.c.ts >= fetch_start,
                        bars_table.c.ts < end_dt,
                    ).order_by(bars_table.c.ts)
                ).fetchall()
            # Compute indicators over full fetch window
            indicators_list = compute_indicators(rows, tf_lower)
            # Compute candlestick patterns; returns list aligned to bars
            patterns_list = compute_patterns(rows, tf_lower)
            # Compute regimes (trend and volatility) causally
            regimes_list = compute_regimes(rows, indicators_list, tf_lower)
            regimes_by_ts = {entry["ts"]: entry for entry in regimes_list}
            # Build mapping from ts to patterns dict for quick lookup
            patterns_by_ts = {entry["ts"]: entry.get("patterns", {}) for entry in patterns_list}
            # Filter rows within requested [start_dt, end_dt)
            rows_to_insert = []
            for ind in indicators_list:
                ts_val = ind["ts"]
                if ts_val is None:
                    continue
                # Ensure timezone aware
                if ts_val.tzinfo is None:
                    ts_val = ts_val.replace(tzinfo=timezone.utc)
                if ts_val >= start_dt and ts_val < end_dt:
                    indicators_json = {
                        "ema20": ind.get("ema20"),
                        "ema50": ind.get("ema50"),
                        "ema200": ind.get("ema200"),
                        "rsi14": ind.get("rsi14"),
                        "atr14": ind.get("atr14"),
                        "vwap": ind.get("vwap"),
                        # Regime labels will be added after computing regimes
                    }
                    pattern_json = patterns_by_ts.get(ts_val, {})
                    # Append regimes into indicators_json
                    regime = regimes_by_ts.get(ts_val, {})
                    indicators_json["trend_regime"] = regime.get("trend_regime", "unknown")
                    indicators_json["vol_regime"] = regime.get("vol_regime", "unknown")
                    rows_to_insert.append(
                        {
                            "symbol": sym,
                            "timeframe": tf_lower,
                            "ts": ts_val,
                            "indicators_json": indicators_json,
                            "patterns_json": pattern_json,
                            "feature_version": feature_version,
                        }
                    )
            # Upsert into DB
            count = upsert_features_snapshot(engine, rows_to_insert)
            # Output summary
            click.echo(
                f"{sym} {tf}: computed {len(indicators_list)} bars, upserted {count} rows, version {feature_version}"
            )


@cli.command()
@click.option(
    "--symbols",
    type=str,
    help="Comma-separated list of symbols to fetch corporate actions for. Must be a subset of the configured whitelist.",
)
@click.option(
    "--start",
    type=str,
    default=None,
    help="Start date in ISO format (YYYY-MM-DD). Default: seven days before today in America/New_York.",
)
@click.option(
    "--end",
    type=str,
    default=None,
    help="End date in ISO format (YYYY-MM-DD). Default: ninety days after today in America/New_York.",
)
def actions(symbols: Optional[str], start: Optional[str], end: Optional[str]) -> None:
    """Ingest corporate actions and upsert them into the database.

    This command fetches corporate action announcements for a list of
    symbols between a start and end date.  It then upserts these
    announcements into the ``corporate_actions`` table, ensuring
    deduplication by symbol, action type and ex-date.
    """
    whitelist = _default_whitelist()
    # Determine requested symbols
    if symbols:
        requested = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        requested = whitelist
    extra = [s for s in requested if s not in whitelist]
    if extra:
        raise click.UsageError(
            f"Requested symbols {extra} are not in the allowed whitelist {whitelist}"
        )
    # Determine start_date and end_date
    from zoneinfo import ZoneInfo
    from datetime import timedelta, date
    tz_ny = ZoneInfo("America/New_York")
    if start:
        try:
            dt = _parse_datetime(start)
            if dt is None:
                raise ValueError
            start_date = dt.date()
        except Exception:
            raise click.UsageError(f"Invalid start date '{start}'")
    else:
        today_ny = datetime.now(tz_ny).date()
        start_date = today_ny - timedelta(days=7)
    if end:
        try:
            dt = _parse_datetime(end)
            if dt is None:
                raise ValueError
            end_date = dt.date()
        except Exception:
            raise click.UsageError(f"Invalid end date '{end}'")
    else:
        today_ny = datetime.now(tz_ny).date()
        end_date = today_ny + timedelta(days=90)
    # Build provider and engine
    provider = _build_provider()
    engine = _build_engine()
    # Fetch and upsert actions
    from informer.ingestion.corporate_actions import ingest_corporate_actions
    fetched, upserted = ingest_corporate_actions(
        provider=provider,
        engine=engine,
        symbols=requested,
        start_date=start_date,
        end_date=end_date,
    )
    # Output summary
    click.echo(
        f"actions: symbols={requested} start={start_date} end={end_date} fetched={fetched} upserted={upserted}"
    )


@cli.command()
@click.option(
    "--symbols",
    type=str,
    help="Comma-separated list of symbols to chart. Must be a subset of the configured whitelist.",
)
@click.option(
    "--timeframes",
    type=str,
    default=None,
    help="Comma-separated list of timeframes to chart (default from TIMEFRAMES env var).",
)
@click.option(
    "--start",
    type=str,
    default=None,
    help="Start date or datetime in ISO format (default uses timeframe-based lookback)",
)
@click.option(
    "--end",
    type=str,
    default=None,
    help="End date or datetime in ISO format (default: now in UTC)",
)
@click.option(
    "--out-dir",
    "out_dir",
    type=str,
    default="artifacts/charts",
    help="Directory to write chart PNG files (default: artifacts/charts)",
)
@click.option(
    "--chart-version",
    "chart_version_opt",
    type=str,
    default=None,
    help="Chart version identifier (default: env CHART_VERSION or the renderer default)",
)
@click.option(
    "--limit",
    "limit_bars",
    type=int,
    default=200,
    help="Maximum number of bars to plot per chart (default: 200)",
)
def charts(
    symbols: Optional[str],
    timeframes: Optional[str],
    start: Optional[str],
    end: Optional[str],
    out_dir: str,
    chart_version_opt: Optional[str],
    limit_bars: int,
) -> None:
    """Generate deterministic PNG candlestick charts for stored bars.

    This command creates charts for each requested symbol and timeframe,
    writing files into a versioned directory structure.  It uses
    stored OHLCV bars, computes indicators and overlays, and renders
    the charts via mplfinance.  The number of plotted bars can be
    limited to the most recent ``--limit`` bars for readability.
    """
    whitelist = _default_whitelist()
    # Determine requested symbols
    if symbols:
        requested = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        requested = whitelist
    # Enforce whitelist subset
    extra = [s for s in requested if s not in whitelist]
    if extra:
        raise click.UsageError(
            f"Requested symbols {extra} are not in the allowed whitelist {whitelist}"
        )
    # Determine timeframes list
    if timeframes:
        tf_list = [tf.strip() for tf in timeframes.split(",") if tf.strip()]
    else:
        tf_list = _default_timeframes()
    # Parse end datetime; default to now UTC
    end_dt = _parse_datetime(end) or datetime.now(timezone.utc)
    # Chart version: option, env var or default
    chart_version = chart_version_opt or os.getenv("CHART_VERSION") or CHART_VERSION_DEFAULT
    # Build engine
    engine = _build_engine()
    # For each timeframe determine start based on option or lookback
    from sqlalchemy import select, func
    from informer.ingestion.bars import bars_table  # import here to avoid circular
    out_path_base = Path(out_dir)
    for tf in tf_list:
        tf_lower = tf.lower()
        # Determine start per timeframe
        if start:
            start_dt = _parse_datetime(start)
            if start_dt is None:
                raise click.UsageError(f"Could not parse start value '{start}'")
        else:
            # Lookback periods per timeframe
            if tf_lower == "15m":
                lookback = timedelta(days=14)
            elif tf_lower == "1h":
                lookback = timedelta(days=60)
            else:
                lookback = timedelta(days=400)
            start_dt = end_dt - lookback
        # For each symbol
        for sym in requested:
            # Render chart
            path = render_chart_for_symbol_timeframe(
                engine=engine,
                symbol=sym,
                timeframe=tf_lower,
                start=start_dt,
                end=end_dt,
                out_dir=out_path_base,
                chart_version=chart_version,
                limit_bars=limit_bars,
            )
            if path is None:
                click.echo(f"{sym} {tf}: SKIP (no data)")
                continue
            # Count bars in [start,end)
            with engine.connect() as conn:
                n_bars = conn.execute(
                    select(func.count()).select_from(bars_table).where(
                        bars_table.c.symbol == sym,
                        bars_table.c.timeframe == tf_lower,
                        bars_table.c.ts >= start_dt,
                        bars_table.c.ts < end_dt,
                    )
                ).scalar()
            click.echo(f"{sym} {tf}: WROTE {path} ({n_bars} bars)")


# -----------------------------------------------------------------------------
# Packet command
#
# This command builds the canonical informer packet for each symbol at a
# specified as-of timestamp.  The packet includes data quality summaries,
# recent bars across multiple timeframes, latest computed features,
# corporate actions and chart references.  Packets are written as JSON
# files into an output directory organised by run_id.  By default,
# missing charts will be rendered on demand; use --no-render-missing-charts
# to disable rendering and mark missing charts as not-ready.


@cli.command()
@click.option(
    "--symbols",
    type=str,
    help="Comma-separated list of symbols to build packets for. Must be a subset of the configured whitelist.",
)
@click.option(
    "--as-of",
    "as_of_opt",
    type=str,
    default=None,
    help="As-of datetime in ISO format (default: now UTC)",
)
@click.option(
    "--timeframes",
    type=str,
    default=None,
    help="Comma-separated list of timeframes to include (default from TIMEFRAMES env var).",
)
@click.option(
    "--limit",
    "limit_bars",
    type=int,
    default=200,
    help="Maximum number of bars per timeframe to include (default: 200)",
)
@click.option(
    "--out-dir",
    "out_dir",
    type=str,
    default="artifacts/packets",
    help="Directory to write packet JSON files (default: artifacts/packets)",
)
@click.option(
    "--charts-dir",
    "charts_dir",
    type=str,
    default="artifacts/charts",
    help="Directory where chart PNG files are stored (default: artifacts/charts)",
)
@click.option(
    "--schema-version",
    "schema_version_opt",
    type=str,
    default=None,
    help="Schema version tag for packets (default: env SCHEMA_VERSION or 'v0.1')",
)
@click.option(
    "--feature-version",
    "feature_version_opt",
    type=str,
    default=None,
    help="Feature version tag to query latest features (default: env FEATURE_VERSION or 'v0.1')",
)
@click.option(
    "--chart-version",
    "chart_version_opt",
    type=str,
    default=None,
    help="Chart version tag to determine chart file locations (default: env CHART_VERSION or renderer default)",
)
@click.option(
    "--run-id",
    "run_id_opt",
    type=str,
    default=None,
    help="Run identifier for this packet generation (default: env RUN_ID or timestamp)",
)
@click.option(
    "--no-render-missing-charts",
    "no_render_missing_charts",
    is_flag=True,
    default=False,
    help="If set, do not attempt to render charts when PNG files are missing; mark as missing instead.",
)
def packet(
    symbols: Optional[str],
    as_of_opt: Optional[str],
    timeframes: Optional[str],
    limit_bars: int,
    out_dir: str,
    charts_dir: str,
    schema_version_opt: Optional[str],
    feature_version_opt: Optional[str],
    chart_version_opt: Optional[str],
    run_id_opt: Optional[str],
    no_render_missing_charts: bool,
) -> None:
    """Build canonical informer packets for one or more symbols.

    Examples::

        python -m informer packet --symbols AAPL,MSFT --as-of 2025-01-15T22:45:00Z --limit 300 --out-dir out/packets

    Packets contain QA summaries, recent bars, latest computed
    indicators and patterns, regime labels, corporate actions and
    references to charts.  They are versioned and include run metadata.
    """
    whitelist = _default_whitelist()
    # Determine requested symbols
    if symbols:
        requested = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        requested = whitelist
    extra = [s for s in requested if s not in whitelist]
    if extra:
        raise click.UsageError(
            f"Requested symbols {extra} are not in the allowed whitelist {whitelist}"
        )
    # Determine timeframe list
    if timeframes:
        tf_list = [tf.strip() for tf in timeframes.split(",") if tf.strip()]
    else:
        tf_list = _default_timeframes()
    # Parse as_of datetime
    as_of_dt = _parse_datetime(as_of_opt) or datetime.now(timezone.utc)
    # Determine schema version
    schema_version = schema_version_opt or os.getenv("SCHEMA_VERSION") or SCHEMA_VERSION_DEFAULT
    # Determine feature version
    feature_version = feature_version_opt or os.getenv("FEATURE_VERSION") or "v0.1"
    # Determine chart version
    chart_version = chart_version_opt or os.getenv("CHART_VERSION") or CHART_VERSION_DEFAULT
    # Determine run_id
    run_id = run_id_opt or os.getenv("RUN_ID")
    if not run_id:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # Build engine
    engine = _build_engine()
    # Prepare output directories
    out_path_base = Path(out_dir)
    charts_path_base = Path(charts_dir)
    run_path = out_path_base / run_id
    run_path.mkdir(parents=True, exist_ok=True)
    # Build packets per symbol
    for sym in requested:
        pkt = build_informer_packet(
            engine=engine,
            symbol=sym,
            as_of=as_of_dt,
            timeframes=tf_list,
            limit_bars=limit_bars,
            feature_version=feature_version,
            chart_version=chart_version,
            charts_dir=charts_path_base,
            run_id=run_id,
            schema_version=schema_version,
            render_missing_charts=not no_render_missing_charts,
        )
        # Serialise to JSON
        json_path = run_path / f"{sym}.json"
        pkt_dict = pkt.model_dump()
        with json_path.open("w", encoding="utf-8") as f:
            json.dump(pkt_dict, f, indent=2, sort_keys=True, default=str)
        click.echo(f"packet: {sym} status={pkt.status} path={json_path}")


# -----------------------------------------------------------------------------
# Healthcheck command
#
# This command performs an end-to-end system health check, verifying Python
# version, dependency availability, environment configuration, database
# connectivity and schema, artifacts directory permissions and symbol
# whitelist enforcement.  The report is printed as a human-readable summary
# followed by an optional JSON representation.


@cli.command()
@click.option(
    "--symbols",
    type=str,
    help="Comma-separated list of symbols to validate (default: env SYMBOLS or whitelist)",
)
@click.option(
    "--timeframes",
    type=str,
    default=None,
    help="Comma-separated list of timeframes (default from TIMEFRAMES env var)",
)
@click.option(
    "--run-id",
    "run_id_opt",
    type=str,
    default=None,
    help="Optional run identifier (default: env RUN_ID or generated timestamp)",
)
@click.option(
    "--schema-version",
    "schema_version_opt",
    type=str,
    default=None,
    help="Schema version identifier (default: env SCHEMA_VERSION or 'v0.1')",
)
@click.option(
    "--feature-version",
    "feature_version_opt",
    type=str,
    default=None,
    help="Feature version identifier (default: env FEATURE_VERSION or 'v0.1')",
)
@click.option(
    "--chart-version",
    "chart_version_opt",
    type=str,
    default=None,
    help="Chart version identifier (default: env CHART_VERSION or renderer default)",
)
@click.option(
    "--artifacts-root",
    "artifacts_root_opt",
    type=str,
    default="artifacts",
    help="Root directory for artifacts (default: artifacts)",
)
@click.option(
    "--strict/--no-strict",
    default=False,
    help="Exit with code 2 when status is NOT_READY (does not change report status)",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    default=False,
    help="If set, also output the report in JSON format",
)
@click.option(
    "--out",
    type=str,
    default=None,
    help="Optional path to write the JSON health report (default is artifacts_root/health/<run_id>.json)",
)
def healthcheck(
    symbols: Optional[str],
    timeframes: Optional[str],
    run_id_opt: Optional[str],
    schema_version_opt: Optional[str],
    feature_version_opt: Optional[str],
    chart_version_opt: Optional[str],
    artifacts_root_opt: str,
    strict: bool,
    json_output: bool,
    out: Optional[str],
) -> None:
    """Run an Informer health check and report results.

    This command performs a series of checks covering environment
    configuration, dependency availability, database connectivity,
    required schema objects and filesystem permissions.  It prints a
    human-readable summary and, when ``--json`` is specified, a JSON
    representation of the report.
    """
    # Determine symbols list
    whitelist = _default_whitelist()
    if symbols:
        requested = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        requested = whitelist
    # Enforce whitelist subset for CLI usage; this duplicates the check later but
    # ensures consistent behaviour with other commands.
    extra = [s for s in requested if s not in whitelist]
    if extra:
        raise click.UsageError(
            f"Requested symbols {extra} are not in the allowed whitelist {whitelist}"
        )
    # Determine timeframes list
    if timeframes:
        tf_list = [tf.strip() for tf in timeframes.split(",") if tf.strip()]
    else:
        tf_list = _default_timeframes()
    # Determine run_id
    run_id = run_id_opt or os.getenv("RUN_ID")
    if not run_id:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # Determine version tags
    schema_version = schema_version_opt or os.getenv("SCHEMA_VERSION") or "v0.1"
    feature_version = feature_version_opt or os.getenv("FEATURE_VERSION") or "v0.1"
    chart_version = chart_version_opt or os.getenv("CHART_VERSION") or CHART_VERSION_DEFAULT
    provider_version = PROVIDER_VERSION
    # Determine database engine.  If DATABASE_URL is unset or engine creation fails,
    # fall back to None and capture any exception for reporting.
    engine = None
    engine_error: Optional[str] = None
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        try:
            engine = _build_engine()
        except Exception as exc:
            engine = None
            engine_error = str(exc)
    else:
        engine = None
    # Set up artifacts root path
    artifacts_root = Path(artifacts_root_opt)
    # Build health report using the (possibly None) engine
    report = build_health_report(
        engine=engine,
        run_id=run_id,
        schema_version=schema_version,
        feature_version=feature_version,
        chart_version=chart_version,
        provider_version=provider_version,
        artifacts_root=artifacts_root,
        symbols=requested,
        timeframes=tf_list,
        strict=strict,
    )
    # Attach engine error detail if present
    if engine_error:
        report.environment["engine_error"] = engine_error
    # Determine report output path
    if out:
        report_path = Path(out)
    else:
        report_path = artifacts_root / "health" / f"{run_id}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    # Write JSON report file
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report.model_dump(), f, indent=2, sort_keys=True, default=str)
    # Count errors and warnings for summary
    error_count = sum(1 for chk in report.checks if chk.severity == "ERROR")
    warn_count = sum(1 for chk in report.checks if chk.severity == "WARN")
    # Print concise human summary
    click.echo(
        f"healthcheck: status={report.status} errors={error_count} warns={warn_count} report={report_path}"
    )
    # Print JSON to stdout if requested
    if json_output:
        click.echo(json.dumps(report.model_dump(), indent=2, sort_keys=True, default=str))
    # Determine exit code for strict mode
    exit_code = 0
    if strict and report.status == "NOT_READY":
        exit_code = 2
    # Exit with appropriate code
    ctx = click.get_current_context()
    ctx.exit(exit_code)


@cli.command()
@click.option(
    "--packets-dir",
    type=str,
    default=None,
    help=(
        "Directory containing packet JSON files. "
        "Defaults to 'artifacts/packets/<run_id>' if not specified."
    ),
)
@click.option(
    "--out-dir",
    type=str,
    default=None,
    help="Directory to write decision JSON files (default: artifacts/decisions)",
)
@click.option(
    "--symbols",
    type=str,
    help=(
        "Comma-separated list of symbols to consider. Must be a subset of the configured whitelist. "
        "Defaults to all symbols in the whitelist."
    ),
)
@click.option(
    "--as-of",
    "as_of_opt",
    type=str,
    default=None,
    help="ISO datetime for the decision reference (default: now UTC)",
)
@click.option(
    "--run-id",
    "run_id_opt",
    type=str,
    default=None,
    help="Optional run identifier (default: env RUN_ID or generated timestamp)",
)
@click.option(
    "--max-candidates",
    type=int,
    default=None,
    help="Maximum number of screener candidates to evaluate (default: 2)",
)
@click.option(
    "--max-risk-usd",
    type=float,
    default=None,
    help="Maximum USD risk per trade (default: env MAX_RISK_USD or 50.0)",
)
@click.option(
    "--cash-usd",
    type=float,
    default=None,
    help="Optional available cash in USD to cap share count",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    default=False,
    help="If set, also output the decision as JSON to stdout",
)
def decide(
    packets_dir: Optional[str],
    out_dir: Optional[str],
    symbols: Optional[str],
    as_of_opt: Optional[str],
    run_id_opt: Optional[str],
    max_candidates: Optional[int],
    max_risk_usd: Optional[float],
    cash_usd: Optional[float],
    json_output: bool,
) -> None:
    """Run the Phase 2 decision pipeline and emit a trade decision.

    This command loads informer packet JSON files, runs a
    deterministic analysis pipeline using a fake LLM, performs
    validation and sizing and enforces a one-trade-per-day lock.  It
    produces a decision JSON file and prints a concise summary.
    """
    # Determine run_id
    run_id = run_id_opt or os.getenv("RUN_ID")
    if not run_id:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # Determine whitelist and requested symbols
    whitelist = _default_whitelist()
    if symbols:
        requested = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        requested = whitelist
    # Enforce whitelist subset
    extra = [s for s in requested if s not in whitelist]
    if extra:
        raise click.UsageError(
            f"Requested symbols {extra} are not in the allowed whitelist {whitelist}"
        )
    # Parse as_of datetime
    as_of = _parse_datetime(as_of_opt) or datetime.now(timezone.utc)
    # Determine packets directory
    if packets_dir:
        packets_path = Path(packets_dir)
    else:
        # default: artifacts/packets/<run_id>
        packets_path = Path("artifacts") / "packets" / run_id
    # Determine output directory
    if out_dir:
        out_path = Path(out_dir)
    else:
        out_path = Path("artifacts") / "decisions"
    out_path.mkdir(parents=True, exist_ok=True)
    # Determine max candidates (cap at 2)
    max_candidates_val = max_candidates if max_candidates is not None else 2
    if max_candidates_val > 2:
        max_candidates_val = 2
    # Determine max_risk_usd
    max_risk_val = None
    if max_risk_usd is not None:
        max_risk_val = max_risk_usd
    else:
        env_val = os.getenv("MAX_RISK_USD")
        if env_val:
            try:
                max_risk_val = float(env_val)
            except Exception:
                max_risk_val = 50.0
        else:
            max_risk_val = 50.0
    # Determine cash_usd if not provided
    if cash_usd is None:
        env_cash = os.getenv("CASH_USD")
        if env_cash:
            try:
                cash_usd = float(env_cash)
            except Exception:
                cash_usd = None
    # Determine trade lock path: under artifacts/state/trade_lock.json relative to out_path parent
    trade_lock_path = out_path.parent / "state" / "trade_lock.json"

    # Determine LLM mode now so that we can perform live preflight before loading packets
    llm_mode = os.getenv("LLM_MODE", "fake").lower()

    # Live-mode preflight: when running with LLM_MODE=live, verify that all required
    # API keys are present before attempting to load packets or initialise any providers.
    # The required keys mirror those checked by the config-check command: Alpaca keys,
    # OpenAI key, and either Gemini or Google API key.  Missing variable names are
    # accumulated into a list which is sorted for deterministic output.  When any are
    # missing a NOT_READY decision is emitted and the CLI exits with status 2 without
    # initialising any LLM clients or running the decision pipeline.
    if llm_mode == "live":
        missing_live_vars: list[str] = []
        if not os.getenv("ALPACA_API_KEY_ID"):
            missing_live_vars.append("ALPACA_API_KEY_ID")
        if not os.getenv("ALPACA_API_SECRET_KEY"):
            missing_live_vars.append("ALPACA_API_SECRET_KEY")
        if not os.getenv("OPENAI_API_KEY"):
            missing_live_vars.append("OPENAI_API_KEY")
        if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
            missing_live_vars.append("GEMINI_API_KEY")
        missing_live_vars.sort()
        if missing_live_vars:
            # Compose a NOT_READY decision with a MISSING_API_KEYS reason code
            from informer.llm.models import ArbiterDecision
            from informer.llm.validator import validate_and_size
            arbiter_decision = ArbiterDecision(
                action="NOT_READY",
                symbol=None,
                entry=None,
                stop=None,
                targets=[],
                confidence=None,
                reason_codes=["MISSING_API_KEYS"],
                notes=None,
            )
            decision_not_ready = validate_and_size(
                arbiter_decision,
                as_of=as_of,
                run_id=run_id,
                whitelist=whitelist,
                max_risk_usd=max_risk_val,
                cash_usd=cash_usd,
            )
            # Attach audit details listing missing variables deterministically
            try:
                decision_not_ready.audit = {"missing_env_vars": missing_live_vars}
            except Exception:
                # Guard against unexpected issues assigning audit; ignore silently
                pass
            # Write the decision JSON to disk
            decision_path = out_path / f"{run_id}.json"
            with decision_path.open("w", encoding="utf-8") as f:
                json.dump(decision_not_ready.model_dump(), f, indent=2, sort_keys=True, default=str)
            # Print a concise error message listing the missing variables
            click.echo(
                "Missing environment variables: " + ", ".join(missing_live_vars),
                err=True,
            )
            # Print summary line consistent with other decide outputs
            sym_display = "-"
            shares_display = "-"
            risk_display = "-"
            click.echo(
                f"decide: action={decision_not_ready.action} symbol={sym_display} shares={shares_display} risk={risk_display} path={decision_path}"
            )
            # Emit JSON on stdout if requested
            if json_output:
                click.echo(
                    json.dumps(
                        decision_not_ready.model_dump(), indent=2, sort_keys=True, default=str
                    )
                )
            # Exit with code 2 (consistent with config-check live failures)
            ctx = click.get_current_context()
            ctx.exit(2)

    # At this point either live preflight passed or we are in fake mode; safe to load packets
    packets = load_packets(packets_path, requested)

    decision: Optional[FinalDecision] = None  # type: ignore[assignment]
    llm: LLMClient
    if llm_mode == "live":
        # Attempt to initialise real provider clients.  If this fails, produce a no‑trade decision
        try:
            from informer.llm.client import RoleRouterLLMClient, OpenAIClient, GeminiClient  # local import
            openai_client = OpenAIClient()
            gemini_client = GeminiClient()
            llm = RoleRouterLLMClient(
                clients={"openai": openai_client, "google": gemini_client},
                fallback_critic=False,
            )
        except Exception as exc:
            # Build reason codes based on the exception message
            reason_codes = ["LIVE_MODE_INIT_FAILED"]
            msg = str(exc).lower()
            if "api_key" in msg or "must be set" in msg:
                reason_codes.append("MISSING_API_KEYS")
            # Compose a no‑trade decision without running the pipeline
            from informer.llm.models import ArbiterDecision
            from informer.llm.validator import validate_and_size
            arbiter_decision = ArbiterDecision(
                action="NO_TRADE",
                symbol=None,
                entry=None,
                stop=None,
                targets=[],
                confidence=None,
                reason_codes=reason_codes,
                notes=None,
            )
            decision = validate_and_size(
                arbiter_decision,
                as_of=as_of,
                run_id=run_id,
                whitelist=whitelist,
                max_risk_usd=max_risk_val,
                cash_usd=cash_usd,
            )
            decision.audit = {"live_init_error": str(exc)}
            llm = FakeLLMClient()  # placeholder; will not be used when decision is preset
    else:
        llm = FakeLLMClient()
    # Only run the pipeline when a decision hasn't been produced already
    if decision is None:
        try:
            decision = run_decision_pipeline(
                packets=packets,
                as_of=as_of,
                run_id=run_id,
                whitelist=whitelist,
                max_candidates=max_candidates_val,
                llm=llm,
                max_risk_usd=max_risk_val,
                cash_usd=cash_usd,
                trade_lock_path=trade_lock_path,
            )
        except Exception:
            # Fail-safe: produce a NO_TRADE decision when any unexpected error occurs
            from informer.llm.models import ArbiterDecision
            from informer.llm.validator import validate_and_size
            arbiter_decision = ArbiterDecision(
                action="NO_TRADE",
                symbol=None,
                entry=None,
                stop=None,
                targets=[],
                confidence=None,
                reason_codes=["LLM_FAILURE"],
                notes=None,
            )
            decision = validate_and_size(
                arbiter_decision,
                as_of=as_of,
                run_id=run_id,
                whitelist=whitelist,
                max_risk_usd=max_risk_val,
                cash_usd=cash_usd,
            )
            decision.audit = {}
    # Before writing the decision JSON, enforce the one‑trade‑per‑day
    # constraint using the database lock.  Only attempt to lock
    # decisions with action ``TRADE``.  If the lock cannot be
    # acquired (i.e., a trade has already been recorded for the same
    # NY trading date), veto the decision into a NO_TRADE with an
    # explicit reason code.  Any unexpected error during lock
    # enforcement is ignored to avoid blocking normal operation.
    try:
        if decision.action == "TRADE":
            # Compute the NY trading date from the decision's as_of timestamp
            from informer.state.trade_lock import get_ny_trading_date, try_acquire_lock
            from informer.db.session import get_engine
            import hashlib
            ny_date = get_ny_trading_date(decision.as_of)
            # Deterministically hash the validated decision payload
            serialized = json.dumps(decision.model_dump(), sort_keys=True, default=str)
            decision_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
            engine = get_engine()
            acquired = try_acquire_lock(engine, ny_date, run_id, decision_hash, decision.symbol)
            if not acquired:
                # Veto the trade: convert to NO_TRADE and add reason code
                new_reasons = list(decision.reason_codes) if decision.reason_codes else []
                if "ALREADY_TRADED_TODAY" not in new_reasons:
                    new_reasons.append("ALREADY_TRADED_TODAY")
                decision.action = "NO_TRADE"
                decision.symbol = None
                decision.entry = None
                decision.stop = None
                decision.targets = []
                decision.shares = None
                decision.risk_usd = None
                decision.r_multiple = None
                decision.confidence = None
                decision.reason_codes = new_reasons
    except Exception as exc:
        # Fail closed on DB lock enforcement errors: veto TRADE into NO_TRADE.
        # If the decision was a trade, convert it to NO_TRADE and add a reason code.
        new_reasons = list(decision.reason_codes) if getattr(decision, "reason_codes", None) else []
        # Append a deterministic failure code if not already present
        if "LOCK_ENFORCEMENT_FAILED" not in new_reasons:
            new_reasons.append("LOCK_ENFORCEMENT_FAILED")
        # Always veto trades on lock enforcement failures
        decision.action = "NO_TRADE"
        # Clear trade-specific fields to maintain schema compatibility
        decision.symbol = None
        decision.entry = None
        decision.stop = None
        decision.targets = []
        decision.shares = None
        decision.risk_usd = None
        decision.r_multiple = None
        decision.confidence = None
        decision.reason_codes = new_reasons
    # Write decision JSON
    decision_path = out_path / f"{run_id}.json"
    with decision_path.open("w", encoding="utf-8") as f:
        json.dump(decision.model_dump(), f, indent=2, sort_keys=True, default=str)
    # Print summary line
    sym_display = decision.symbol if decision.symbol is not None else "-"
    shares_display = (
        str(decision.shares) if decision.shares is not None else "-"
    )
    risk_display = (
        f"{decision.risk_usd:.2f}" if decision.risk_usd is not None else "-"
    )
    click.echo(
        f"decide: action={decision.action} symbol={sym_display} shares={shares_display} risk={risk_display} path={decision_path}"
    )
    # Print JSON to stdout if requested
    if json_output:
        click.echo(json.dumps(decision.model_dump(), indent=2, sort_keys=True, default=str))


@cli.command()
@click.option(
    "--decision-file",
    "decision_file",
    type=click.Path(exists=True, dir_okay=False, readable=True),
    required=True,
    help="Path to the JSON decision file produced by the decide command.",
)
def notify(decision_file: str) -> None:
    """Send a Telegram notification for a trade decision.

    This command reads a decision artifact generated by the ``decide``
    command and sends a formatted message via Telegram when the
    decision action is ``TRADE``.  No notification is sent for
    ``NO_TRADE`` decisions or when required environment variables are
    missing.  The Telegram bot token, target chat ID and allowlist
    must be configured via environment variables (see docs).  A
    deduplication key derived from the trade parameters is used to
    prevent duplicate notifications.

    Example::

        python -m informer notify --decision-file artifacts/decisions/<run_id>.json

    """
    import json

    # Load the decision JSON
    with open(decision_file, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except Exception:
            click.echo(f"[error] Could not parse decision file: {decision_file}")
            return
    # Determine run mode from environment; suppress notification in shadow or backtest modes
    run_mode = os.getenv("JARVIS_RUN_MODE", "live").lower()
    # No notification for non-trade decisions or when run mode is not live
    if data.get("action") != "TRADE" or run_mode in {"shadow", "backtest"}:
        # Exit quietly when there is no trade or in non-live mode
        return
    # Gather required fields; missing fields are represented as '-'
    symbol = data.get("symbol") or "-"
    entry = data.get("entry")
    stop = data.get("stop")
    targets = data.get("targets", [])
    shares = data.get("shares")
    risk_usd = data.get("risk_usd")
    r_multiple = data.get("r_multiple")
    confidence = data.get("confidence")
    trade_date_ny = data.get("trade_date_ny") or "-"
    run_id = data.get("run_id") or "-"
    reason_codes = data.get("reason_codes", [])
    # Format the notification text
    lines = []
    lines.append(f"📈 JARVIS Trade Alert")
    lines.append(f"Symbol: {symbol}")
    lines.append(f"Entry: {entry}  Stop: {stop}")
    lines.append(f"Targets: {', '.join(str(t) for t in targets) if targets else '-'}")
    lines.append(f"Shares: {shares if shares is not None else '-'}  Risk: {risk_usd:.2f} USD" if risk_usd is not None else f"Shares: {shares if shares is not None else '-'}  Risk: -")
    lines.append(f"R-multiple: {r_multiple if r_multiple is not None else '-'}  Confidence: {confidence if confidence is not None else '-'}")
    lines.append(f"Trade Date (NY): {trade_date_ny}")
    lines.append(f"Run ID: {run_id}")
    if reason_codes:
        lines.append(f"Reason Codes: {', '.join(reason_codes)}")
    message_text = "\n".join(lines)
    # Determine Telegram credentials from environment
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        # Missing configuration: skip sending
        return
    # Build dedupe key from trade parameters (date, symbol, entry, stop)
    dedupe_key_parts = [str(trade_date_ny), str(symbol), str(entry), str(stop)]
    dedupe_key = "_".join(part.replace(" ", "").replace("/", "-") for part in dedupe_key_parts)
    # Send the message; ignore return value
    try:
        telegram.send_message(token=token, chat_id=chat_id, text=message_text, dedupe_key=dedupe_key)
    except Exception:
        # Fail silently; do not raise errors from the notification
        pass


# Register forwardtest CLI group
cli.add_command(forwardtest, name="forwardtest")

# Register prop CLI group (evaluation utilities)
cli.add_command(prop, name="prop")

# Register the verify‑dod‑b command after the CLI group is defined.  The
# command itself is decorated with @click.command and must be added
# explicitly to avoid a NameError when importing this module before
# the cli group exists.
cli.add_command(verify_dod_b)


@cli.command()
@click.option(
    "--start",
    required=True,
    type=str,
    help="Start date for the backtest in YYYY-MM-DD format.",
)
@click.option(
    "--end",
    required=True,
    type=str,
    help="End date for the backtest in YYYY-MM-DD format (inclusive).",
)
@click.option(
    "--symbols",
    type=str,
    default=None,
    help="Comma-separated subset of symbols to backtest. Must be a subset of the allowlist.",
)
@click.option(
    "--initial-cash",
    type=float,
    default=100_000.0,
    show_default=True,
    help="Initial cash balance in USD.",
)
@click.option(
    "--decision-time",
    type=str,
    default="10:15",
    show_default=True,
    help="Decision time in HH:MM (local to decision-tz).",
)
@click.option(
    "--decision-tz",
    type=str,
    default="America/New_York",
    show_default=True,
    help="Timezone for decision time (e.g., America/New_York).",
)
@click.option(
    "--out-dir",
    type=str,
    default=None,
    help="Directory to write backtest artifacts (default: artifacts/backtests/<run_id>).",
)
@click.option(
    "--slippage-bps",
    type=float,
    default=2.0,
    show_default=True,
    help="Slippage in basis points applied per side (default: 2.0).",
)
@click.option(
    "--commission-per-share",
    type=float,
    default=0.0,
    show_default=True,
    help="Commission per share per side (default: 0.0).",
)
def backtest(
    start: str,
    end: str,
    symbols: Optional[str],
    initial_cash: float,
    decision_time: str,
    decision_tz: str,
    out_dir: Optional[str],
    slippage_bps: float,
    commission_per_share: float,
) -> None:
    """Run an intraday backtest over a date range.

    This command loads 15-minute bar data from the database for the
    specified symbols and date range, runs the baseline backtest
    engine and writes CSV/JSON artifacts into the output directory.
    """
    # Parse dates
    try:
        start_date = datetime.fromisoformat(start).date()
        end_date = datetime.fromisoformat(end).date()
    except Exception:
        raise click.UsageError("Start and end must be in YYYY-MM-DD format")
    if start_date > end_date:
        raise click.UsageError("Start date must be <= end date")
    # Determine symbols
    whitelist = _default_whitelist()
    if symbols:
        requested = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        requested = whitelist
    extra = [s for s in requested if s not in whitelist]
    if extra:
        raise click.UsageError(
            f"Requested symbols {extra} are not in the allowed whitelist {whitelist}"
        )
    symbols_list = sorted(set(requested))
    # Parse decision_time
    if not decision_time or ":" not in decision_time:
        raise click.UsageError("decision-time must be in HH:MM format")
    try:
        hour, minute = [int(x) for x in decision_time.split(":")]
        dec_time = _time(hour, minute)
    except Exception:
        raise click.UsageError("Invalid decision-time; expected HH:MM")
    # Build config
    cfg = BacktestConfig(
        symbols=symbols_list,
        start_date=start_date,
        end_date=end_date,
        initial_cash=initial_cash,
        decision_time=dec_time,
        decision_tz=decision_tz,
    )
    # Determine output directory
    run_id = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    if out_dir:
        out_path = Path(out_dir)
    else:
        out_path = Path("artifacts") / "backtests" / run_id
    # Load bars from the database
    engine = _build_engine()
    from sqlalchemy import select, and_  # import locally to avoid global dependency
    from informer.ingestion.bars import bars_table  # import the table definition
    # Determine start and end datetimes in UTC for bar retrieval.  Compute
    # a warmup start date that looks back sufficiently far to satisfy
    # the required warmup bars for 15m bars.  Bars outside the
    # timeframe will be ignored by the engine when slicing.
    tzinfo = ZoneInfo(decision_tz)
    warmup_date = _compute_warmup_start_date(start_date, "15m")
    start_dt_local = datetime.combine(warmup_date, _time(0, 0)).replace(tzinfo=tzinfo)
    start_dt_utc = start_dt_local.astimezone(ZoneInfo("UTC"))
    # Use the existing _time alias for consistency; the bare ``time`` is not imported
    # at this scope, so refer to `_time` for constructing 23:59 time
    end_dt_local = datetime.combine(end_date, _time(23, 59)).replace(tzinfo=tzinfo)
    end_dt_utc = end_dt_local.astimezone(ZoneInfo("UTC"))
    bars_map: Dict[str, List[Dict[str, Any]]] = {sym: [] for sym in symbols_list}
    with engine.connect() as conn:
        for sym in symbols_list:
            rows = conn.execute(
                select(
                    bars_table.c.ts,
                    bars_table.c.open,
                    bars_table.c.high,
                    bars_table.c.low,
                    bars_table.c.close,
                    bars_table.c.volume,
                ).where(
                    and_(
                        bars_table.c.symbol == sym,
                        bars_table.c.timeframe == "15m",
                        bars_table.c.ts >= start_dt_utc,
                        bars_table.c.ts <= end_dt_utc,
                    )
                ).order_by(bars_table.c.ts)
            ).fetchall()
            b_list: List[Dict[str, Any]] = []
            for row in rows:
                # Convert to plain dict with UTC ts
                ts = row.ts
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=ZoneInfo("UTC"))
                b_list.append(
                    {
                        "ts": ts,
                        "open": float(row.open),
                        "high": float(row.high),
                        "low": float(row.low),
                        "close": float(row.close),
                        "volume": float(row.volume),
                    }
                )
            bars_map[sym] = b_list
    # Run engine
    # Build cost model with user‑supplied parameters
    cost_model = CostModel(slippage_bps=slippage_bps, commission_per_share=commission_per_share)
    engine_bt = BacktestEngine(config=cfg, cost_model=cost_model)
    result = engine_bt.run(bars_map)
    # Write artifacts
    out_path.mkdir(parents=True, exist_ok=True)
    write_trades_csv(result.trades, str(out_path / "trades.csv"))
    write_equity_curve_csv(result.equity_curve, str(out_path / "equity_curve.csv"))
    write_reasons_csv(result.reasons, str(out_path / "reasons.csv"))
    write_summary_json(result.summary, cfg, str(out_path / "summary.json"), cost_model=cost_model)
    click.echo(f"Backtest completed. Artifacts written to {out_path}")


# ---------------------------------------------------------------------------
# Phase 3: Parameter sweep CLI
@cli.command(name="backtest-sweep")
@click.option(
    "--start",
    required=True,
    help="Start date in YYYY-MM-DD format",
)
@click.option(
    "--end",
    required=True,
    help="End date in YYYY-MM-DD format",
)
@click.option(
    "--symbols",
    type=str,
    default=None,
    help="Comma‑separated list of symbols (default: whitelist)",
)
@click.option(
    "--decision-time",
    type=str,
    default="10:15",
    help="Decision time in HH:MM (local)",
)
@click.option(
    "--decision-tz",
    type=str,
    default="America/New_York",
    help="Timezone for decision time",
)
@click.option(
    "--out-dir",
    type=str,
    default=None,
    help="Output directory for sweep artifacts",
)
@click.option(
    "--k-stop-grid",
    type=str,
    default="1.0,1.5,2.0",
    help="Comma‑separated grid for k_stop values",
)
@click.option(
    "--k-target-grid",
    type=str,
    default="2.0,3.0,4.0",
    help="Comma‑separated grid for k_target values",
)
@click.option(
    "--score-threshold-grid",
    type=str,
    default="0.0,0.5,1.0",
    help="Comma‑separated grid for score_threshold values",
)
@click.option(
    "--objective",
    type=str,
    default="avg_r",
    help="Objective metric to maximize (avg_r, expectancy_r, total_pnl, profit_factor, win_rate, max_drawdown_pct)",
)
@click.option(
    "--slippage-bps",
    type=float,
    default=2.0,
    show_default=True,
    help="Slippage in basis points applied per side (default: 2.0).",
)
@click.option(
    "--commission-per-share",
    type=float,
    default=0.0,
    show_default=True,
    help="Commission per share per side (default: 0.0).",
)
def backtest_sweep(
    start: str,
    end: str,
    symbols: Optional[str],
    decision_time: str,
    decision_tz: str,
    out_dir: Optional[str],
    k_stop_grid: str,
    k_target_grid: str,
    score_threshold_grid: str,
    objective: str,
    slippage_bps: float,
    commission_per_share: float,
) -> None:
    """Run a parameter grid sweep over the specified date range.

    This command loads bar data from the database, evaluates all
    parameter combinations on the entire window, ranks the runs by the
    specified objective and writes summary artifacts to disk.
    """
    # Parse dates
    try:
        start_date = datetime.fromisoformat(start).date()
        end_date = datetime.fromisoformat(end).date()
    except Exception:
        raise click.UsageError("Start and end must be in YYYY-MM-DD format")
    if start_date > end_date:
        raise click.UsageError("Start date must be <= end date")
    # Determine symbols
    whitelist = _default_whitelist()
    if symbols:
        requested = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        requested = whitelist
    extras = [s for s in requested if s not in whitelist]
    if extras:
        raise click.UsageError(
            f"Requested symbols {extras} are not in the allowed whitelist {whitelist}"
        )
    symbols_list = sorted(set(requested))
    # Parse decision time
    if not decision_time or ":" not in decision_time:
        raise click.UsageError("decision-time must be in HH:MM format")
    try:
        hr, mn = [int(x) for x in decision_time.split(":")]
        dec_time = _time(hr, mn)
    except Exception:
        raise click.UsageError("Invalid decision-time; expected HH:MM")
    # Parse grids
    def _parse_grid(val: str) -> List[float]:
        parts = [p.strip() for p in val.split(",") if p.strip()]
        try:
            return [float(x) for x in parts]
        except Exception:
            raise click.UsageError(f"Could not parse grid values '{val}'")
    ks_grid = _parse_grid(k_stop_grid)
    kt_grid = _parse_grid(k_target_grid)
    st_grid = _parse_grid(score_threshold_grid)
    param_spec = {
        "k_stop": ks_grid,
        "k_target": kt_grid,
        "score_threshold": st_grid,
    }
    # Validate objective
    valid_objectives = {
        "avg_r",
        "expectancy_r",
        "total_pnl",
        "profit_factor",
        "win_rate",
        "max_drawdown_pct",
    }
    if objective not in valid_objectives:
        raise click.UsageError(f"Invalid objective '{objective}'; must be one of {sorted(valid_objectives)}")
    # Build base config
    cfg = BacktestConfig(
        symbols=symbols_list,
        start_date=start_date,
        end_date=end_date,
        decision_time=dec_time,
        decision_tz=decision_tz,
    )
    # Determine output directory
    run_id = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    if out_dir:
        out_path = Path(out_dir)
    else:
        out_path = Path("artifacts") / "sweeps" / run_id
    out_path.mkdir(parents=True, exist_ok=True)
    # Load bars with warmup buffer
    engine_db = _build_engine()
    from sqlalchemy import select, and_  # deferred import for optional dependency
    from informer.ingestion.bars import bars_table
    tzinfo = ZoneInfo(decision_tz)
    # Compute warmup start date to load enough pre‑start history.  We
    # look back across trading days according to the required warmup
    # bars for 15m bars.
    warmup_date = _compute_warmup_start_date(start_date, "15m")
    warmup_start_dt_local = datetime.combine(warmup_date, _time(0, 0)).replace(tzinfo=tzinfo)
    start_dt_utc = warmup_start_dt_local.astimezone(ZoneInfo("UTC"))
    end_dt_local = datetime.combine(end_date, _time(23, 59)).replace(tzinfo=tzinfo)
    end_dt_utc = end_dt_local.astimezone(ZoneInfo("UTC"))
    bars_map: Dict[str, List[Dict[str, Any]]] = {sym: [] for sym in symbols_list}
    with engine_db.connect() as conn:
        for sym in symbols_list:
            rows = conn.execute(
                select(
                    bars_table.c.ts,
                    bars_table.c.open,
                    bars_table.c.high,
                    bars_table.c.low,
                    bars_table.c.close,
                    bars_table.c.volume,
                ).where(
                    and_(
                        bars_table.c.symbol == sym,
                        bars_table.c.timeframe == "15m",
                        bars_table.c.ts >= start_dt_utc,
                        bars_table.c.ts <= end_dt_utc,
                    )
                ).order_by(bars_table.c.ts)
            ).fetchall()
            b_list: List[Dict[str, Any]] = []
            for row in rows:
                ts = row.ts
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=ZoneInfo("UTC"))
                b_list.append(
                    {
                        "ts": ts,
                        "open": float(row.open),
                        "high": float(row.high),
                        "low": float(row.low),
                        "close": float(row.close),
                        "volume": float(row.volume),
                    }
                )
            bars_map[sym] = b_list
    # Build cost model and run parameter sweep.  top_n=0 returns all parameter combinations so
    # that sweep_results.csv includes one row per param set.
    cost_model = CostModel(slippage_bps=slippage_bps, commission_per_share=commission_per_share)
    sweep_results, best_info, best_result = run_parameter_sweep(
        bars_map,
        cfg,
        param_spec,
        objective,
        start_date=start_date,
        end_date=end_date,
        top_n=0,
        cost_model=cost_model,
    )
    # Write sweep_results.csv.  Always write a header row based on the parameter
    # names and a stable set of metric keys.  If no results are present (which
    # should not occur when a non‑empty grid is provided), write only the
    # header.  Sort results deterministically by parameter values (k_stop, k_target,
    # score_threshold, extras) to ensure stable row ordering regardless of
    # objective or dictionary ordering.
    import csv
    sweep_csv_path = out_path / "sweep_results.csv"
    param_keys = sorted(param_spec.keys())
    metric_keys: List[str]
    if sweep_results:
        metric_keys = list(sweep_results[0]["metrics"].keys())
    else:
        # Define a minimal stable set of metric keys for header when no results.
        metric_keys = [
            "trades",
            "win_rate",
            "total_pnl",
            "max_drawdown",
            "max_drawdown_pct",
            "expectancy_r",
            "profit_factor",
        ]
    header = param_keys + metric_keys
    # Define a deterministic sort key for parameter ordering.  Use the same
    # ordering rules as the tie‑break in run_parameter_sweep: sort by k_stop,
    # k_target, score_threshold ascending, then by JSON of extras.
    def _param_sort_key(entry: Dict[str, Any]) -> tuple:
        p = entry["params"]
        ks = p.get("k_stop")
        kt = p.get("k_target")
        st = p.get("score_threshold")
        ks_val = float("inf") if ks is None else ks
        kt_val = float("inf") if kt is None else kt
        st_val = float("inf") if st is None else st
        extras = {k: v for k, v in p.items() if k not in {"k_stop", "k_target", "score_threshold"}}
        extras_str = json.dumps(extras, sort_keys=True)
        return (ks_val, kt_val, st_val, extras_str)
    sweep_results_sorted = sorted(sweep_results, key=_param_sort_key)
    with sweep_csv_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for entry in sweep_results_sorted:
            row: List[Any] = []
            for k in param_keys:
                row.append(entry["params"].get(k))
            m = entry["metrics"]
            for mk in metric_keys:
                row.append(m.get(mk))
            writer.writerow(row)
    # Write best_params.json
    # Persist the cost model assumptions alongside the best parameters and metrics so that
    # sweep artifacts are self‑documenting.  Include the CLI‑provided cost settings
    # regardless of whether defaults were used.  Keys are written in a deterministic
    # order to preserve reproducibility of the file contents.
    best_params_path = out_path / "best_params.json"
    with best_params_path.open("w") as f:
        # Assemble the cost_model block using the slippage and commission values passed
        # via CLI.  These values originate from the constructed CostModel earlier.
        json.dump(
            {
                "objective": objective,
                "best_params": best_info.get("params", {}),
                "best_metrics": best_info.get("metrics", {}),
                "cost_model": {
                    "slippage_bps": slippage_bps,
                    "commission_per_share": commission_per_share,
                },
            },
            f,
            indent=2,
        )
    # Write best_run artifacts
    if best_result is not None:
        best_dir = out_path / "best_run"
        best_dir.mkdir(parents=True, exist_ok=True)
        # Build config for summary reflecting chosen params
        best_cfg_kwargs = cfg.__dict__.copy()
        best_cfg_kwargs.update(best_info.get("params", {}))
        best_cfg = BacktestConfig(**best_cfg_kwargs)
        write_trades_csv(best_result.trades, str(best_dir / "trades.csv"))
        write_equity_curve_csv(best_result.equity_curve, str(best_dir / "equity_curve.csv"))
        write_reasons_csv(best_result.reasons, str(best_dir / "reasons.csv"))
        # Persist cost model assumptions in summary
        write_summary_json(best_result.summary, best_cfg, str(best_dir / "summary.json"), cost_model=cost_model)
    click.echo(f"Parameter sweep completed. Artifacts written to {out_path}")


# ---------------------------------------------------------------------------
# Phase 3: Walk‑forward validation CLI
@cli.command(name="backtest-walkforward")
@click.option(
    "--start",
    required=True,
    help="Start date in YYYY-MM-DD format",
)
@click.option(
    "--end",
    required=True,
    help="End date in YYYY-MM-DD format",
)
@click.option(
    "--train-days",
    type=int,
    required=True,
    help="Number of trading days in the training window",
)
@click.option(
    "--test-days",
    type=int,
    required=True,
    help="Number of trading days in the test window",
)
@click.option(
    "--symbols",
    type=str,
    default=None,
    help="Comma‑separated list of symbols (default: whitelist)",
)
@click.option(
    "--decision-time",
    type=str,
    default="10:15",
    help="Decision time in HH:MM",
)
@click.option(
    "--decision-tz",
    type=str,
    default="America/New_York",
    help="Timezone for decision time",
)
@click.option(
    "--out-dir",
    type=str,
    default=None,
    help="Output directory for walk‑forward artifacts",
)
@click.option(
    "--k-stop-grid",
    type=str,
    default="1.0,1.5,2.0",
    help="Comma‑separated grid for k_stop",
)
@click.option(
    "--k-target-grid",
    type=str,
    default="2.0,3.0,4.0",
    help="Comma‑separated grid for k_target",
)
@click.option(
    "--score-threshold-grid",
    type=str,
    default="0.0,0.5,1.0",
    help="Comma‑separated grid for score_threshold",
)
@click.option(
    "--objective",
    type=str,
    default="avg_r",
    help="Objective metric to maximize (avg_r, expectancy_r, total_pnl, profit_factor, win_rate, max_drawdown_pct)",
)
@click.option(
    "--holdout-start",
    type=str,
    default=None,
    help="Start date of explicit holdout period (YYYY-MM-DD)",
)
@click.option(
    "--holdout-days",
    type=int,
    default=None,
    help="Number of trading days in holdout period (alternative to holdout-start)",
)
@click.option(
    "--step-days",
    type=int,
    default=None,
    help="Stride between fold starts (default: test-days)",
)
@click.option(
    "--slippage-bps",
    type=float,
    default=2.0,
    show_default=True,
    help="Slippage in basis points applied per side (default: 2.0).",
)
@click.option(
    "--commission-per-share",
    type=float,
    default=0.0,
    show_default=True,
    help="Commission per share per side (default: 0.0).",
)
def backtest_walkforward(
    start: str,
    end: str,
    train_days: int,
    test_days: int,
    symbols: Optional[str],
    decision_time: str,
    decision_tz: str,
    out_dir: Optional[str],
    k_stop_grid: str,
    k_target_grid: str,
    score_threshold_grid: str,
    objective: str,
    holdout_start: Optional[str],
    holdout_days: Optional[int],
    step_days: Optional[int],
    slippage_bps: float,
    commission_per_share: float,
) -> None:
    """Run a walk‑forward validation with optional holdout period.

    For each fold, a parameter sweep is run over the training window to
    select the best parameters; that parameter set is then evaluated
    over the subsequent test window.  Results across all test windows
    are aggregated and written along with an optional holdout evaluation.
    """
    # Parse dates
    try:
        start_date = datetime.fromisoformat(start).date()
        end_date = datetime.fromisoformat(end).date()
    except Exception:
        raise click.UsageError("Start and end must be in YYYY-MM-DD format")
    if start_date > end_date:
        raise click.UsageError("Start date must be <= end date")
    if train_days < 1 or test_days < 1:
        raise click.UsageError("train-days and test-days must be positive")
    # Determine symbols
    whitelist = _default_whitelist()
    if symbols:
        requested = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        requested = whitelist
    extras = [s for s in requested if s not in whitelist]
    if extras:
        raise click.UsageError(
            f"Requested symbols {extras} are not in the allowed whitelist {whitelist}"
        )
    symbols_list = sorted(set(requested))
    # Parse decision time
    if not decision_time or ":" not in decision_time:
        raise click.UsageError("decision-time must be in HH:MM format")
    try:
        hr, mn = [int(x) for x in decision_time.split(":")]
        dec_time = _time(hr, mn)
    except Exception:
        raise click.UsageError("Invalid decision-time; expected HH:MM")
    # Parse grids
    def _parse_grid(val: str) -> List[float]:
        parts = [p.strip() for p in val.split(",") if p.strip()]
        try:
            return [float(x) for x in parts]
        except Exception:
            raise click.UsageError(f"Could not parse grid values '{val}'")
    ks_grid = _parse_grid(k_stop_grid)
    kt_grid = _parse_grid(k_target_grid)
    st_grid = _parse_grid(score_threshold_grid)
    param_spec = {
        "k_stop": ks_grid,
        "k_target": kt_grid,
        "score_threshold": st_grid,
    }
    # Validate objective
    valid_objectives = {
        "avg_r",
        "expectancy_r",
        "total_pnl",
        "profit_factor",
        "win_rate",
        "max_drawdown_pct",
    }
    if objective not in valid_objectives:
        raise click.UsageError(f"Invalid objective '{objective}'; must be one of {sorted(valid_objectives)}")
    # Parse holdout start
    holdout_start_date: Optional[date] = None
    if holdout_start:
        try:
            holdout_start_date = datetime.fromisoformat(holdout_start).date()
        except Exception:
            raise click.UsageError("holdout-start must be in YYYY-MM-DD format")
    # Build base config
    cfg = BacktestConfig(
        symbols=symbols_list,
        start_date=start_date,
        end_date=end_date,
        decision_time=dec_time,
        decision_tz=decision_tz,
    )
    # Determine output directory
    run_id = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    if out_dir:
        out_path = Path(out_dir)
    else:
        out_path = Path("artifacts") / "walkforward" / run_id
    out_path.mkdir(parents=True, exist_ok=True)
    # Load bars with warmup buffer
    engine_db = _build_engine()
    from sqlalchemy import select, and_  # deferred import
    from informer.ingestion.bars import bars_table
    tzinfo = ZoneInfo(decision_tz)
    # Compute warmup start date to load enough pre‑start history.  We
    # look back across trading days according to the required warmup
    # bars for 15m bars.
    warmup_date = _compute_warmup_start_date(start_date, "15m")
    warmup_start_dt_local = datetime.combine(warmup_date, _time(0, 0)).replace(tzinfo=tzinfo)
    start_dt_utc = warmup_start_dt_local.astimezone(ZoneInfo("UTC"))
    end_dt_local = datetime.combine(end_date, _time(23, 59)).replace(tzinfo=tzinfo)
    end_dt_utc = end_dt_local.astimezone(ZoneInfo("UTC"))
    bars_map: Dict[str, List[Dict[str, Any]]] = {sym: [] for sym in symbols_list}
    with engine_db.connect() as conn:
        for sym in symbols_list:
            rows = conn.execute(
                select(
                    bars_table.c.ts,
                    bars_table.c.open,
                    bars_table.c.high,
                    bars_table.c.low,
                    bars_table.c.close,
                    bars_table.c.volume,
                ).where(
                    and_(
                        bars_table.c.symbol == sym,
                        bars_table.c.timeframe == "15m",
                        bars_table.c.ts >= start_dt_utc,
                        bars_table.c.ts <= end_dt_utc,
                    )
                ).order_by(bars_table.c.ts)
            ).fetchall()
            b_list: List[Dict[str, Any]] = []
            for row in rows:
                ts = row.ts
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=ZoneInfo("UTC"))
                b_list.append(
                    {
                        "ts": ts,
                        "open": float(row.open),
                        "high": float(row.high),
                        "low": float(row.low),
                        "close": float(row.close),
                        "volume": float(row.volume),
                    }
                )
            bars_map[sym] = b_list
    # Run walkforward
    # Build cost model for walk-forward runs
    cost_model = CostModel(slippage_bps=slippage_bps, commission_per_share=commission_per_share)
    wf_result = run_walkforward(
        bars_map,
        cfg,
        start_date=start_date,
        end_date=end_date,
        train_days=train_days,
        test_days=test_days,
        param_spec=param_spec,
        objective=objective,
        step_days=step_days,
        holdout_start=holdout_start_date,
        holdout_days=holdout_days,
        cost_model=cost_model,
    )
    # Write walkforward_folds.csv
    import csv
    folds_csv_path = out_path / "walkforward_folds.csv"
    folds = wf_result.get("folds", [])
    # Always write the folds CSV with a header.  If there are no folds,
    # write only the header row; otherwise write one row per fold.
    base_keys = ["fold_id", "train_start", "train_end", "test_start", "test_end"]
    metric_keys = [
        "trades",
        "win_rate",
        "total_pnl",
        "max_drawdown",
        "max_drawdown_pct",
    ]
    header = base_keys + ["params", "train_objective"] + metric_keys
    with folds_csv_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for row in folds:
            params_json = json.dumps(row.get("params", {}), sort_keys=True)
            test_metrics = row.get("test_metrics", {})
            csv_row = [
                row.get("fold_id"),
                row.get("train_start"),
                row.get("train_end"),
                row.get("test_start"),
                row.get("test_end"),
                params_json,
                row.get("train_objective"),
            ]
            for mk in metric_keys:
                csv_row.append(test_metrics.get(mk))
            writer.writerow(csv_row)
    # Write oos_trades.csv (always write header even if no trades)
    oos_trades = wf_result.get("oos_trades", [])
    write_trades_csv(oos_trades, str(out_path / "oos_trades.csv"))
    # Write oos_summary.json: ensure a summary is produced even if empty
    oos_summary = wf_result.get("oos_summary")
    from informer.backtest.metrics import compute_summary, compute_regime_breakdown
    if not oos_summary:
        # Compute default summary for zero trades
        oos_summary = compute_summary([], [])
        oos_summary["regime_breakdown"] = compute_regime_breakdown([])
    # Persist cost model and versioning metadata in the OOS summary.  The summary
    # dictionary contains aggregate metrics and a regime breakdown.  We augment
    # this structure with a `meta` block that records the universe and
    # validation versions as well as the cost model assumptions used.  Use
    # dictionary unpacking to preserve existing keys while appending `meta` at
    # the end for deterministic ordering.
    oos_meta = {
        "universe_version": UNIVERSE_VERSION,
        "validation_version": "v1",
        "cost_model": {
            "slippage_bps": slippage_bps,
            "commission_per_share": commission_per_share,
        },
    }
    oos_summary_with_meta = {**oos_summary, "meta": oos_meta}
    with (out_path / "oos_summary.json").open("w") as f:
        json.dump(oos_summary_with_meta, f, indent=2)
    # Write holdout artifacts if holdout is enabled
    holdout_trades = wf_result.get("holdout_trades", [])
    holdout_summary = wf_result.get("holdout_summary")
    if holdout_start_date or holdout_days is not None:
        # Always write holdout trades (empty header if none)
        write_trades_csv(holdout_trades, str(out_path / "holdout_trades.csv"))
        if not holdout_summary:
            holdout_summary = compute_summary([], [])
            holdout_summary["regime_breakdown"] = compute_regime_breakdown([])
        # Persist metadata in the holdout summary analogous to the OOS summary.  Record
        # universe/version identifiers and the cost model.  Place meta at the end
        # of the dictionary to maintain stable key ordering.
        holdout_meta = {
            "universe_version": UNIVERSE_VERSION,
            "validation_version": "v1",
            "cost_model": {
                "slippage_bps": slippage_bps,
                "commission_per_share": commission_per_share,
            },
        }
        holdout_summary_with_meta = {**holdout_summary, "meta": holdout_meta}
        with (out_path / "holdout_summary.json").open("w") as f:
            json.dump(holdout_summary_with_meta, f, indent=2)
    # Write run_config.json
    run_cfg = {
        "universe_version": UNIVERSE_VERSION,
        "validation_version": "v1",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "train_days": train_days,
        "test_days": test_days,
        "step_days": step_days,
        "objective": objective,
        "param_spec": param_spec,
        "symbols": symbols_list,
        "decision_time": decision_time,
        "decision_tz": decision_tz,
    }
    if holdout_start_date:
        run_cfg["holdout_start"] = holdout_start_date.isoformat()
    if holdout_days is not None:
        run_cfg["holdout_days"] = holdout_days
    # Persist cost model assumptions for walk-forward
    run_cfg["cost_model"] = {
        "slippage_bps": slippage_bps,
        "commission_per_share": commission_per_share,
    }
    with (out_path / "run_config.json").open("w") as f:
        json.dump(run_cfg, f, indent=2)
    click.echo(f"Walk-forward validation completed. Artifacts written to {out_path}")

# -----------------------------------------------------------------------------
# Daily scan and scheduler commands
#
# Import orchestration helpers.  These imports are placed here at the end
# of the module to avoid triggering circular imports when this file is
# imported by the orchestration modules.
from .orchestration.daily_scan import run_daily_scan as _run_daily_scan  # noqa: E402
from .orchestration.scheduler import run_scheduler as _run_scheduler  # noqa: E402
# Note: run_smoke_test is imported lazily inside the smoke-test command to avoid circular imports.


@cli.command(name="daily-scan")
@click.option("--run-id", default=None, help="Run identifier (defaults to UTC timestamp)")
@click.option("--as-of", default=None, help="As-of timestamp (defaults to now UTC)")
@click.option(
    "--run-mode",
    default="live",
    type=click.Choice(["live", "shadow"], case_sensitive=False),
    help="Run mode: live or shadow",
)
def daily_scan_cli(run_id: Optional[str], as_of: Optional[str], run_mode: str) -> None:
    """Run the end‑to‑end JARVIS daily scan.

    This command orchestrates a full trading day pipeline including
    health checks, ingestion, corporate actions, QA, feature
    computation, chart rendering, packet assembly, decision making,
    optional notification and forward‑testing.  The pipeline is
    executed in pure Python so that it works on Windows without
    reliance on Bash scripts.  Failures in intermediate steps are
    logged but do not abort the run.  A decision JSON file is
    always written under ``artifacts/decisions/<run_id>.json``.
    """
    _run_daily_scan(run_id=run_id, as_of=as_of, run_mode=run_mode)


@cli.command(name="scheduler")
@click.option("--once", is_flag=True, help="Run exactly one daily scan then exit")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the next run time and exit without sleeping or running",
)
@click.option(
    "--run-mode",
    default="shadow",
    type=click.Choice(["shadow", "live"], case_sensitive=False),
    help="Run mode to pass to the daily scan (default: shadow)",
)
def scheduler_cli(once: bool, dry_run: bool, run_mode: str) -> None:
    """Run the DST‑safe local scheduler.

    This command computes the next run time at 10:15 in the timezone
    configured by ``JARVIS_SCAN_TZ`` (default ``America/New_York``)
    and then sleeps until that time before executing ``jarvis
    daily-scan`` with the specified run mode.  When invoked with
    ``--once`` it performs a single run and exits; otherwise it repeats
    forever.  When invoked with ``--dry-run`` it prints the next run
    time in America/New_York, UTC and Asia/Singapore along with the
    command it would execute and then exits without sleeping or
    running the scan.
    """
    _run_scheduler(once=once, dry_run=dry_run, run_mode=run_mode)


# -----------------------------------------------------------------------------
# Smoke test command
#
# The smoke-test command runs a deterministic, offline verification of the
# JARVIS pipeline using a local SQLite database.  It exercises database
# migrations, health checks, the daily scan (shadow mode) and the scheduler
# dry-run.  The command prints a summary of each step and exits with code
# 0 on success or raises a ClickException on failure.


@cli.command(name="smoke-test")
@click.option(
    "--db-path",
    default="jarvis_smoke.db",
    help=(
        "Path to the SQLite database used during the smoke test (default: jarvis_smoke.db)."
    ),
)
@click.option(
    "--run-id",
    default=None,
    help=(
        "Optional run identifier for the smoke test.  Defaults to a timestamp"
    ),
)
@click.option(
    "--keep",
    is_flag=True,
    default=False,
    help=(
        "If set, retain the SQLite database after the smoke test.  By default"
        " the database file is deleted."
    ),
)
def smoke_test_cli(db_path: str, run_id: Optional[str], keep: bool) -> None:
    """Run an offline smoke test of the JARVIS pipeline.

    This command executes a deterministic sequence of steps to verify
    that JARVIS has been installed correctly and can run end‑to‑end on
    a local machine without network connectivity.  The test uses a
    SQLite database, performs a healthcheck, runs the daily scan in
    shadow mode and computes the next scheduled run via the scheduler
    dry‑run.  It prints a concise summary and exits with status 0 on
    success; any failure results in a ClickException with a non‑zero
    exit code.
    """
    # Import the smoke test helper lazily to avoid circular imports.  This import
    # is inside the function so that ``informer.orchestration.smoke`` can
    # import this module without triggering recursive imports.
    from .orchestration.smoke import run_smoke_test as _run_smoke_test  # type: ignore
    success = _run_smoke_test(db_path=db_path, run_id=run_id, keep=keep)
    if not success:
        raise click.ClickException("Smoke test failed")


# -----------------------------------------------------------------------------
# Trade lock administrative commands
#
# These commands provide visibility into and control over the persistent
# one‑trade‑per‑day lock stored in the database.  The ``lock-status``
# command prints information about a lock for a specific New York date,
# while ``lock-clear`` removes a lock (useful for testing).  Both
# commands operate on the database referenced by the ``DATABASE_URL``
# environment variable and require that migrations have been applied.


@cli.command(name="lock-status")
@click.option(
    "--date",
    "date_str",
    required=True,
    help="NY trading date (YYYY-MM-DD) to query",
)
def lock_status_cli(date_str: str) -> None:
    """Print the lock status for a given America/New_York trading date.

    This command queries the ``trade_day_lock`` table for the provided
    date and prints whether a lock exists.  If locked, it reports
    the run ID, symbol and timestamp at which the lock was recorded.
    """
    from datetime import date as _date
    try:
        ny_date = _date.fromisoformat(date_str)
    except Exception:
        raise click.UsageError(f"Invalid date format: {date_str}; expected YYYY-MM-DD")
    from informer.db.session import get_engine
    from informer.state.trade_lock import get_lock_details
    try:
        engine = get_engine()
    except Exception as exc:
        raise click.ClickException(f"Could not connect to database: {exc}")
    details = get_lock_details(engine, ny_date)
    if not details:
        click.echo(f"No lock for {ny_date.isoformat()}")
    else:
        ny_date_db, run_id, decision_hash, symbol, locked_at = details  # type: ignore[misc]
        symbol_display = symbol if symbol else "-"
        ts_display = locked_at.isoformat() if locked_at else "-"
        click.echo(
            f"Locked {ny_date_db} by run_id={run_id} symbol={symbol_display} locked_at={ts_display}"
        )


@cli.command(name="lock-clear")
@click.option(
    "--date",
    "date_str",
    required=True,
    help="NY trading date (YYYY-MM-DD) to clear",
)
@click.option(
    "--i-understand",
    is_flag=True,
    required=True,
    help="Acknowledge that clearing the lock may allow another trade for the given date",
)
def lock_clear_cli(date_str: str, i_understand: bool) -> None:
    """Clear the lock for a given America/New_York trading date.

    This command deletes any row in ``trade_day_lock`` for the
    specified date.  It requires the ``--i-understand`` flag as a
    safety measure.  If the table or row does not exist the
    command succeeds silently.
    """
    if not i_understand:
        raise click.UsageError("This command is destructive; use --i-understand to confirm")
    from datetime import date as _date
    try:
        ny_date = _date.fromisoformat(date_str)
    except Exception:
        raise click.UsageError(f"Invalid date format: {date_str}; expected YYYY-MM-DD")
    from informer.db.session import get_engine
    from informer.state.trade_lock import clear_lock
    try:
        engine = get_engine()
    except Exception as exc:
        raise click.ClickException(f"Could not connect to database: {exc}")
    clear_lock(engine, ny_date)
    click.echo(f"Cleared lock for {ny_date.isoformat()}")