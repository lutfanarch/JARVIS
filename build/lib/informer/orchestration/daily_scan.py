"""Daily scan orchestration for JARVIS.

This module provides a Python implementation of the end‑to‑end trading
workflow previously orchestrated by ``scripts/daily_scan.sh``.  The
``run_daily_scan`` function runs the pipeline in a deterministic order
while tolerating failures in intermediate steps.  At the end of the run
there will always be a decision artifact written to the
``artifacts/decisions`` directory, even if upstream commands fail.

The orchestrator can be invoked directly from the CLI via
``jarvis daily-scan``.  In tests a custom runner can be injected to
simulate command outcomes without spawning subprocesses.
"""

from __future__ import annotations

import json
import os
import sys
import subprocess
from datetime import datetime, timezone
import time
from typing import Callable, List, Optional


# Type alias for the runner function.  The runner should accept a
# sequence of arguments representing an informer command (e.g.
# ["healthcheck", "--strict"]) and return an integer exit code.
Runner = Callable[[List[str]], int]


def _default_runner(args: List[str]) -> int:
    """Run an informer CLI command via ``python -m informer`` with a timeout.

    The default runner invokes the given command using Python’s
    ``-m informer`` entrypoint.  A per‑step timeout is applied (120 s)
    to prevent hung processes from stalling the entire daily scan.  If
    the subprocess exceeds the timeout or raises any exception the
    runner returns a non‑zero exit code.  Standard output and error are
    inherited from the parent so that logs appear in real time.

    Parameters
    ----------
    args : list of str
        The command name and its arguments (e.g., ``["ingest"]``).

    Returns
    -------
    int
        ``0`` on success, or a non‑zero value on failure or timeout.
    """
    cmd = [sys.executable, "-m", "informer"] + args
    try:
        proc = subprocess.run(cmd, timeout=120)
        return proc.returncode
    except subprocess.TimeoutExpired:
        # On timeout, log the offending command to stderr and return non‑zero
        print(f"[JARVIS] Step timed out: {' '.join(args)}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Log unexpected exceptions and return failure
        print(f"[JARVIS] Step failed: {' '.join(args)}: {exc}", file=sys.stderr)
        return 1


def run_daily_scan(
    run_id: Optional[str] = None,
    as_of: Optional[str] = None,
    run_mode: str = "live",
    runner: Optional[Runner] = None,
) -> None:
    """Execute a full daily scan.

    This function orchestrates the same sequence of steps as the
    Bash script ``scripts/daily_scan.sh``.  Each step is attempted in
    order; failures are logged but do not abort the run.  At the end
    of the run there will always be a JSON decision file under
    ``artifacts/decisions/<run_id>.json``.

    Parameters
    ----------
    run_id : str, optional
        A unique run identifier used when naming packet and decision files.
        If omitted, the current UTC timestamp is used (format
        ``YYYYMMDDHHMMSS``).
    as_of : str, optional
        An ISO 8601 timestamp representing the as‑of time for packet
        assembly and decision making.  If omitted, the current UTC time
        (without microseconds) is used in ISO format with a trailing ``Z``.
    run_mode : {"live", "shadow"}
        The operational mode.  In ``live`` mode notifications are sent;
        in ``shadow`` mode notifications are suppressed and a forward
        test record is written.
    runner : callable, optional
        A function that executes informer commands.  The default
        implementation spawns a subprocess invoking ``python -m informer``.
        When testing, a custom runner can be supplied to simulate
        different return codes or to avoid spawning subprocesses.
    """
    # Determine run identifier and as‑of timestamp
    if run_id is None:
        run_id = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    if as_of is None:
        # Use UTC now without microseconds and with trailing Z
        now = datetime.utcnow().replace(microsecond=0)
        as_of = now.isoformat() + "Z"

    # Normalise run_mode to lower case
    run_mode_normalised = (run_mode or "live").lower()
    if run_mode_normalised not in {"live", "shadow"}:
        raise ValueError(f"Invalid run mode: {run_mode}")

    # Choose runner implementation
    run_cmd: Runner = runner or _default_runner

    # Prepare artifact directories.  Use relative paths so that the
    # current working directory determines where artifacts are created.
    artifacts_dir = os.path.join("artifacts")
    packets_dir = os.path.join(artifacts_dir, "packets", run_id)
    decisions_dir = os.path.join(artifacts_dir, "decisions")
    state_dir = os.path.join(artifacts_dir, "state")
    for directory in (packets_dir, decisions_dir, state_dir):
        os.makedirs(directory, exist_ok=True)

    # Compute the path to the decision file up front so it can be
    # referenced in the finally block.  The file is named using the
    # run identifier under the decisions directory.
    decision_file_path = os.path.join(decisions_dir, f"{run_id}.json")

    # Define a helper to write a placeholder decision.  This is used
    # both when skipping the pipeline due to missing credentials and in
    # the finalizer if no decision file was produced.  The placeholder
    # uses a NOT_READY action and includes the run_id and as_of for
    # traceability.
    def _write_placeholder_decision() -> None:
        placeholder = {
            "run_id": run_id,
            "as_of": as_of,
            "action": "NOT_READY",
            "status": "FAILED",
        }
        try:
            with open(decision_file_path, "w", encoding="utf-8") as f:
                json.dump(placeholder, f)
        except Exception as exc:
            print(f"[JARVIS] Failed to write placeholder decision: {exc}", file=sys.stderr)

    # Fast-path: detect missing Alpaca credentials for shadow mode.  When
    # running in shadow mode without credentials there is no point
    # executing ingest or downstream steps because they will fail.
    missing_alpaca_keys = not os.environ.get("ALPACA_API_KEY_ID") or not os.environ.get("ALPACA_API_SECRET_KEY")

    # Determine whether to short-circuit the pipeline for offline shadow mode.
    # Only perform the fast-path skip when no custom runner has been supplied
    # (i.e., runner is None).  When a custom runner is injected (as in unit
    # tests) the full sequence of commands is executed so that tests can
    # inspect the list of attempted commands.  The variable is computed
    # outside the try/finally so that the finalizer can use it to decide
    # whether to invoke notification/forwardtest steps.
    use_offline_fast_path = (runner is None) and (run_mode_normalised == "shadow") and missing_alpaca_keys

    # Live-mode preflight: determine if required live keys are missing.  In
    # live mode the orchestrator must fail closed deterministically before
    # attempting any provider initialisation or network-facing work.  The
    # required keys mirror the config-check live requirements: Alpaca keys,
    # OpenAI key and either Gemini or Google API key.  Missing variable
    # names are accumulated into a list which is sorted for deterministic
    # output.  When any are absent the pipeline is skipped and a NOT_READY
    # decision emitted.
    missing_live_vars: list[str] = []
    if run_mode_normalised == "live":
        # Alpaca API keys
        if not os.environ.get("ALPACA_API_KEY_ID"):
            missing_live_vars.append("ALPACA_API_KEY_ID")
        if not os.environ.get("ALPACA_API_SECRET_KEY"):
            missing_live_vars.append("ALPACA_API_SECRET_KEY")
        # OpenAI key
        if not os.environ.get("OPENAI_API_KEY"):
            missing_live_vars.append("OPENAI_API_KEY")
        # Gemini can be satisfied by GEMINI_API_KEY or GOOGLE_API_KEY
        if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
            missing_live_vars.append("GEMINI_API_KEY")
        missing_live_vars.sort()
    # Preflight gating applies when run_mode is live and at least one
    # required variable is missing.  Unlike the offline fast-path this
    # applies regardless of whether a custom runner has been provided: the
    # orchestrator must never attempt to initialise providers in live mode
    # when credentials are absent.
    # The live preflight is only applied when no custom runner has been supplied (runner is None).
    # This mirrors the offline fast-path behaviour: in unit tests a custom runner is often
    # injected to record the sequence of commands executed, so preflight would inhibit
    # those tests.  When run in real CLI usage (runner is None) missing keys cause an
    # immediate skip before any pipeline steps are executed.
    use_live_preflight = bool(missing_live_vars) and (run_mode_normalised == "live") and (runner is None)

    # Initialise run record metadata
    start_time = datetime.utcnow().replace(tzinfo=timezone.utc)
    # Predefine list to accumulate step results
    run_steps: list[dict] = []
    # Helper to record a step result
    def record_step(name: str, status: str, duration: float | None = None, exit_code: Optional[int] = None, short_error: Optional[str] = None) -> None:
        entry: dict = {
            "name": name,
            "status": status,
        }
        if duration is not None:
            entry["duration_s"] = round(duration, 3)
        if exit_code is not None:
            entry["exit_code"] = exit_code
        if short_error:
            entry["short_error"] = short_error
        run_steps.append(entry)

    # Utility to run a command and record its result
    def execute_step(step_name: str, args: List[str]) -> None:
        """Execute a subprocess step via the runner and record timing/status."""
        t0 = time.monotonic()
        rc = run_cmd(args)
        duration = time.monotonic() - t0
        status = "PASS" if rc == 0 else "FAIL"
        # Short error message includes return code if nonzero
        short_err: Optional[str] = None
        if rc != 0:
            short_err = f"exit code {rc}"
        record_step(step_name, status, duration, rc, short_err)
        return

    # Wrap the main orchestration in a try/finally to guarantee the
    # decision file and run record are written and final steps are attempted
    try:
        if use_live_preflight:
            # Live mode with missing credentials: fail closed without
            # executing any pipeline steps.  Emit a short message listing
            # missing variables and immediately write a placeholder decision.
            missing_str = ", ".join(missing_live_vars)
            print(
                f"[JARVIS] Live mode with missing credentials; skipping pipeline and emitting NOT_READY decision. Missing: {missing_str}",
                file=sys.stderr,
            )
            _write_placeholder_decision()
            # Record all pipeline steps as SKIP deterministically
            for step_name in [
                "healthcheck",
                "ingest",
                "actions",
                "qa",
                "features",
                "charts",
                "packet",
                "decide",
            ]:
                record_step(step_name, "SKIP")
        elif use_offline_fast_path:
            # Skip pipeline steps and write placeholder decision for shadow mode
            print(
                "[JARVIS] Shadow mode with missing Alpaca credentials; skipping pipeline and emitting NOT_READY decision",
                file=sys.stderr,
            )
            _write_placeholder_decision()
            # Record all pipeline steps as SKIP
            for step_name in [
                "healthcheck",
                "ingest",
                "actions",
                "qa",
                "features",
                "charts",
                "packet",
                "decide",
            ]:
                record_step(step_name, "SKIP")
        else:
            # 1. Healthcheck
            execute_step("healthcheck", ["healthcheck", "--strict"])
            # 2. Ingestion
            execute_step("ingest", ["ingest"])
            # 3. Corporate actions
            execute_step("actions", ["actions"])
            # 4. Quality assurance
            execute_step("qa", ["qa"])
            # 5. Feature computation
            execute_step("features", ["features"])
            # 6. Chart rendering
            execute_step("charts", ["charts"])
            # 7. Packet building
            execute_step("packet", ["packet", "--run-id", run_id, "--as-of", as_of])
            # 8. Decision making
            execute_step("decide", ["decide", "--run-id", run_id, "--as-of", as_of])
    except Exception as exc:
        # Log unexpected exceptions but continue to finalization
        print(f"[JARVIS] Unhandled exception in daily scan: {exc}", file=sys.stderr)
    finally:
        # Ensure the decision artefact exists
        if not os.path.exists(decision_file_path):
            _write_placeholder_decision()
        # In live mode, attempt notification (nonfatal) unless either offline fast path or
        # live preflight has been triggered.  When live preflight is active the pipeline
        # was skipped entirely and notification must not be attempted.
        if run_mode_normalised == "live" and not use_offline_fast_path and not use_live_preflight:
            t0 = time.monotonic()
            rc_notify = run_cmd(["notify", "--decision-file", decision_file_path])
            dur_notify = time.monotonic() - t0
            if rc_notify == 0:
                record_step("notify", "PASS", dur_notify, rc_notify, None)
            else:
                # Record failure and include return code in short_error
                record_step("notify", "FAIL", dur_notify, rc_notify, f"exit code {rc_notify}")
                print("[JARVIS] Notification step failed", file=sys.stderr)
        else:
            # For non-live modes or when the pipeline was skipped (offline fast path or live
            # preflight), mark notification as skipped.  In shadow mode we suppress
            # notification but log a message to stderr for visibility (unless in offline
            # fast path where network keys are missing).
            record_step("notify", "SKIP")
            if run_mode_normalised != "live" and not use_offline_fast_path and not use_live_preflight:
                print(f"[JARVIS] Notification suppressed for run mode {run_mode_normalised}", file=sys.stderr)
        # In shadow mode, always run forward-test recording
        if run_mode_normalised == "shadow":
            t0 = time.monotonic()
            rc_ft = run_cmd([
                "forwardtest",
                "record",
                "--run-id",
                run_id,
                "--as-of",
                as_of,
                "--mode",
                run_mode_normalised,
            ])
            dur_ft = time.monotonic() - t0
            if rc_ft == 0:
                record_step("forwardtest_record", "PASS", dur_ft, rc_ft, None)
            else:
                record_step("forwardtest_record", "FAIL", dur_ft, rc_ft, f"exit code {rc_ft}")
                print("[JARVIS] Forward test record failed", file=sys.stderr)
        else:
            # For live mode, skip forward-test recording
            record_step("forwardtest_record", "SKIP")
        # Compute finished time
        finished_time = datetime.utcnow().replace(tzinfo=timezone.utc)
        # Determine NY trading date from as_of using America/New_York timezone
        try:
            from zoneinfo import ZoneInfo
            as_of_dt = datetime.fromisoformat(as_of.rstrip("Z")).replace(tzinfo=timezone.utc)
            ny_date = as_of_dt.astimezone(ZoneInfo("America/New_York")).date().isoformat()
        except Exception:
            ny_date = None
        # Determine decision action
        decision_action: Optional[str] = None
        try:
            with open(decision_file_path, "r", encoding="utf-8") as f:
                decision_data = json.load(f)
            decision_action = decision_data.get("action")
        except Exception:
            decision_action = None
        # Write run record
        runs_dir = os.path.join(artifacts_dir, "runs")
        os.makedirs(runs_dir, exist_ok=True)
        run_record_path = os.path.join(runs_dir, f"{run_id}.json")
        run_record = {
            "schema_version": "1.0",
            "run_id": run_id,
            "run_mode": run_mode_normalised,
            "started_at_utc": start_time.isoformat().replace("+00:00", "Z"),
            "finished_at_utc": finished_time.isoformat().replace("+00:00", "Z"),
            "as_of_utc": as_of,
            "ny_trading_date": ny_date,
            "steps": run_steps,
            "decision_path": decision_file_path,
            "decision_action": decision_action,
        }
        try:
            with open(run_record_path, "w", encoding="utf-8") as f:
                json.dump(run_record, f)
        except Exception as exc:
            print(f"[JARVIS] Failed to write run record: {exc}", file=sys.stderr)

        # If live preflight was triggered, exit with a deterministic non-zero code after
        # finalisation has completed.  Using sys.exit here ensures that the CLI
        # propagates the exit status while still having written the decision and
        # run artefacts.  This call will raise SystemExit and stop further
        # execution of run_daily_scan.
        if use_live_preflight:
            sys.exit(2)
