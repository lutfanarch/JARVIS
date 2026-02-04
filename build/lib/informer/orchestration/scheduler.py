"""DST‑safe local scheduler for JARVIS.

This module implements a simple always‑on scheduler that triggers the
daily scan at 10:15 local time in the configured timezone on
weekdays.  The scheduler uses the standard library's ``zoneinfo``
module to handle daylight saving transitions correctly.  A dry‑run
mode prints the next scheduled run time without sleeping or executing
the scan.
"""

from __future__ import annotations

import os
import sys
import time
import subprocess
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Optional

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except ImportError:  # pragma: no cover
    # Fall back to tzinfo from dateutil if available.  This branch is
    # unlikely to be executed under supported Python versions but is
    # provided for completeness.
    from dateutil.tz import gettz as ZoneInfo  # type: ignore

from .daily_scan import run_daily_scan


def compute_next_run(now: Optional[datetime] = None, tz_name: Optional[str] = None) -> datetime:
    """Compute the next scheduled run time for the daily scan.

    The scheduler triggers at 10:15 local time in the configured
    timezone on weekdays (Monday–Friday).  If the current time is
    before 10:15 on a weekday, the next run will be today; otherwise
    it will be the next weekday.  Weekends are skipped.  Daylight
    saving transitions are handled by the underlying ``ZoneInfo``
    implementation.

    Parameters
    ----------
    now : datetime, optional
        The current time as a timezone‑aware datetime.  If not
        supplied, ``datetime.now(timezone.utc)`` is used.
    tz_name : str, optional
        The IANA timezone name (e.g. ``"America/New_York"``).  If
        omitted, the environment variable ``JARVIS_SCAN_TZ`` is used;
        otherwise the default is ``"America/New_York"``.

    Returns
    -------
    datetime
        The next run time expressed in UTC.  The returned datetime
        always has timezone ``timezone.utc``.
    """
    # Determine the current time in UTC
    if now is None:
        now_utc = datetime.now(timezone.utc)
    else:
        if now.tzinfo is None:
            # Assume naive datetimes are UTC
            now_utc = now.replace(tzinfo=timezone.utc)
        else:
            now_utc = now.astimezone(timezone.utc)

    # Determine target timezone
    tz_str = tz_name or os.environ.get("JARVIS_SCAN_TZ", "America/New_York")
    try:
        tz = ZoneInfo(tz_str)
    except Exception:
        # If the timezone cannot be loaded, fall back to UTC
        tz = timezone.utc  # type: ignore

    # Convert current time to target timezone
    now_local = now_utc.astimezone(tz)

    # Define the scheduled local time
    scheduled_time = dtime(hour=10, minute=15)

    # Determine candidate run time for today
    candidate_local = datetime(
        now_local.year,
        now_local.month,
        now_local.day,
        scheduled_time.hour,
        scheduled_time.minute,
        tzinfo=tz,
    )

    # If today is a weekday and we haven't passed the scheduled time yet
    if now_local.weekday() < 5 and now_local < candidate_local:
        next_run_local = candidate_local
    else:
        # Compute the next weekday.  Weekdays are 0=Monday .. 6=Sunday.
        days_ahead = 1
        next_date = now_local.date() + timedelta(days=days_ahead)
        # Skip weekends
        while next_date.weekday() >= 5:
            next_date += timedelta(days=1)
        next_run_local = datetime(
            next_date.year,
            next_date.month,
            next_date.day,
            scheduled_time.hour,
            scheduled_time.minute,
            tzinfo=tz,
        )

    # Return the run time in UTC
    return next_run_local.astimezone(timezone.utc)


def run_scheduler(
    once: bool = False,
    dry_run: bool = False,
    tz_name: Optional[str] = None,
    run_mode: str = "shadow",
    runner: Optional[callable] = None,
) -> None:
    """Run the DST‑aware scheduler.

    The scheduler computes the next run time according to
    :func:`compute_next_run`, sleeps until that time, and then
    executes ``jarvis daily-scan`` with the specified run mode.
    This process repeats indefinitely unless ``once`` is true.  The
    scheduler prints the next scheduled run time in multiple
    timezones (America/New_York, UTC and Asia/Singapore) to aid
    operators in different regions.  In ``dry_run`` mode no sleeping
    or execution occurs; the computed next run time and the exact
    command that would be run are printed and the function returns.

    Parameters
    ----------
    once : bool
        If true, perform a single scan then exit.  Otherwise repeat.
    dry_run : bool
        If true, only compute and print the next run time then exit.
    tz_name : str, optional
        Override the timezone used for scheduling.  If not provided,
        the environment variable ``JARVIS_SCAN_TZ`` or the default
        ``"America/New_York"`` is used for computing the next run time.
    run_mode : {"shadow", "live"}
        The run mode to pass through to the daily scan.  Defaults to
        ``"shadow"``.  This option is ignored when a custom runner
        is supplied; tests can inspect the passed arguments to runner.
    runner : callable, optional
        Optional function to invoke the daily scan.  If not provided,
        the scheduler will spawn a subprocess calling
        ``python -m informer daily-scan --run-mode <run_mode>``.
        Tests may supply a stub.  When a runner is supplied it will
        receive a single list of arguments corresponding to the
        ``jarvis`` command after the script name (e.g.,
        ``["daily-scan", "--run-mode", run_mode]``).
    """
    # Determine timezone string
    tz_str = tz_name or os.environ.get("JARVIS_SCAN_TZ", "America/New_York")
    # Determine the runner.  Use subprocess if none provided.  When
    # invoking via subprocess we explicitly include the run-mode
    # parameter so that the daily scan honours the scheduler's
    # configuration.  When a custom runner is supplied it is
    # called with the arguments that would be passed to ``jarvis``
    # after the script name; tests can examine these arguments.
    if runner is None:
        def _run_daily_scan_subprocess() -> None:
            subprocess.run([sys.executable, "-m", "informer", "daily-scan", "--run-mode", run_mode])
        call_daily_scan = _run_daily_scan_subprocess
    else:
        def _call_runner() -> None:
            runner(["daily-scan", "--run-mode", run_mode])
        call_daily_scan = _call_runner

    while True:
        # Compute next run time in UTC
        next_run_utc = compute_next_run(now=None, tz_name=tz_str)
        # Always display the next run in multiple timezones.  The
        # scheduler uses compute_next_run to determine the UTC time at
        # which the next daily scan should run.  Convert this to
        # America/New_York, UTC, and Asia/Singapore for display.  The
        # timezone abbreviations (e.g., EST/EDT, UTC, SGT) are derived
        # from ZoneInfo and may vary depending on DST rules.
        ny_time = next_run_utc.astimezone(ZoneInfo("America/New_York"))
        sgt_time = next_run_utc.astimezone(ZoneInfo("Asia/Singapore"))
        next_run_line = (
            f"Next run at {ny_time.strftime('%Y-%m-%d %H:%M:%S %Z')} / "
            f"{next_run_utc.strftime('%Y-%m-%d %H:%M:%S UTC')} / "
            f"{sgt_time.strftime('%Y-%m-%d %H:%M:%S %Z')}"
        )
        print(next_run_line, flush=True)
        if dry_run:
            # Print the command that would be executed, including run-mode
            print(f"Would run: jarvis daily-scan --run-mode {run_mode}", flush=True)
            return
        # Sleep until next_run_utc
        now_utc = datetime.now(timezone.utc)
        sleep_seconds = (next_run_utc - now_utc).total_seconds()
        if sleep_seconds > 0:
            # Sleep in chunks to allow interrupts
            time.sleep(sleep_seconds)
        # Execute the daily scan
        call_daily_scan()
        if once:
            break
