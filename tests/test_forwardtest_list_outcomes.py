"""Tests for forward‑test listing with outcome information.

This module verifies the behaviour of the ``forwardtest list`` command
when the ``--with-outcomes`` and ``--only-missing-outcomes`` flags
are supplied.  It constructs a temporary forward-test registry and
outcomes log, runs the CLI for different combinations of flags, and
checks that outcome presence, realised PnL/R metrics and filtering
operate as specified.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from informer.cli import cli


def _setup_runs_and_outcomes(tmpdir: Path) -> None:
    """Create sample runs and outcomes for testing list CLI.

    This helper writes three TRADE run entries and one non‑TRADE entry
    into the forward_test registry, along with matching decision
    artefacts.  It then logs two outcome records: one with a realised
    entry price (to test outcome override) and one without an entry
    (to test fallback to the decision entry).  The third TRADE run is
    left without an outcome to exercise the missing outcome behaviour.
    """
    ft_dir = tmpdir / "artifacts" / "forward_test"
    ft_dir.mkdir(parents=True, exist_ok=True)
    runs_path = ft_dir / "forward_test_runs.jsonl"
    outcomes_path = ft_dir / "forward_test_outcomes.jsonl"

    # Define three TRADE runs on consecutive dates with different symbols
    runs = []
    # Run A: outcome with entry present
    run_a_id = "runA"
    ny_date_a = "2026-04-01"
    symbol_a = "AAA"
    created_a = "2026-04-01T10:00:00Z"
    artifact_a = ft_dir / ny_date_a / run_a_id
    artifact_a.mkdir(parents=True, exist_ok=True)
    decision_a = {"entry": 100.0, "stop": 95.0, "shares": 2, "symbol": symbol_a, "action": "TRADE"}
    with (artifact_a / "decision.json").open("w", encoding="utf-8") as f:
        json.dump(decision_a, f)
    runs.append({
        "run_id": run_a_id,
        "ny_date": ny_date_a,
        "created_at_utc": created_a,
        "mode": "shadow",
        "symbols": [symbol_a],
        "decision_status": "TRADE",
        "selected_symbol": symbol_a,
        "rationale_summary": None,
        "schema_version": None,
        "config_hash": "hashA",
        "artifact_dir": str(artifact_a),
        "lock_key": "lockA",
    })

    # Run B: outcome without entry (fallback to decision entry)
    run_b_id = "runB"
    ny_date_b = "2026-04-02"
    symbol_b = "BBB"
    created_b = "2026-04-02T10:00:00Z"
    artifact_b = ft_dir / ny_date_b / run_b_id
    artifact_b.mkdir(parents=True, exist_ok=True)
    decision_b = {"entry": 50.0, "stop": 45.0, "shares": 4, "symbol": symbol_b, "action": "TRADE"}
    with (artifact_b / "decision.json").open("w", encoding="utf-8") as f:
        json.dump(decision_b, f)
    runs.append({
        "run_id": run_b_id,
        "ny_date": ny_date_b,
        "created_at_utc": created_b,
        "mode": "shadow",
        "symbols": [symbol_b],
        "decision_status": "TRADE",
        "selected_symbol": symbol_b,
        "rationale_summary": None,
        "schema_version": None,
        "config_hash": "hashB",
        "artifact_dir": str(artifact_b),
        "lock_key": "lockB",
    })

    # Run C: no outcome logged
    run_c_id = "runC"
    ny_date_c = "2026-04-03"
    symbol_c = "CCC"
    created_c = "2026-04-03T10:00:00Z"
    artifact_c = ft_dir / ny_date_c / run_c_id
    artifact_c.mkdir(parents=True, exist_ok=True)
    decision_c = {"entry": 75.0, "stop": 70.0, "shares": 3, "symbol": symbol_c, "action": "TRADE"}
    with (artifact_c / "decision.json").open("w", encoding="utf-8") as f:
        json.dump(decision_c, f)
    runs.append({
        "run_id": run_c_id,
        "ny_date": ny_date_c,
        "created_at_utc": created_c,
        "mode": "shadow",
        "symbols": [symbol_c],
        "decision_status": "TRADE",
        "selected_symbol": symbol_c,
        "rationale_summary": None,
        "schema_version": None,
        "config_hash": "hashC",
        "artifact_dir": str(artifact_c),
        "lock_key": "lockC",
    })

    # Non‑TRADE run (for completeness); should have '-' in outcome columns
    run_d_id = "runD"
    ny_date_d = "2026-04-04"
    symbol_d = None
    created_d = "2026-04-04T10:00:00Z"
    artifact_d = ft_dir / ny_date_d / run_d_id
    artifact_d.mkdir(parents=True, exist_ok=True)
    decision_d = {"entry": None, "stop": None, "shares": None, "symbol": symbol_d, "action": "NO_TRADE"}
    with (artifact_d / "decision.json").open("w", encoding="utf-8") as f:
        json.dump(decision_d, f)
    runs.append({
        "run_id": run_d_id,
        "ny_date": ny_date_d,
        "created_at_utc": created_d,
        "mode": "shadow",
        "symbols": [],
        "decision_status": "NO_TRADE",
        "selected_symbol": symbol_d,
        "rationale_summary": None,
        "schema_version": None,
        "config_hash": "hashD",
        "artifact_dir": str(artifact_d),
        "lock_key": "lockD",
    })

    # Write runs to registry file
    with runs_path.open("w", encoding="utf-8") as f:
        for rec in runs:
            json.dump(rec, f, sort_keys=True)
            f.write("\n")

    # Write outcomes: one with entry and one without
    outcomes = [
        {
            "ny_date": ny_date_a,
            "symbol": symbol_a,
            "entry": 101.0,  # different from decision entry
            "exit": 110.0,
            "notes": "realised entry present",
            "recorded_at_utc": "2026-04-05T00:00:00Z",
        },
        {
            "ny_date": ny_date_b,
            "symbol": symbol_b,
            # no entry key => fallback to decision entry (50.0)
            "exit": 55.0,
            "notes": "no entry provided",
            "recorded_at_utc": "2026-04-05T01:00:00Z",
        },
    ]
    with outcomes_path.open("w", encoding="utf-8") as f:
        for rec in outcomes:
            json.dump(rec, f, sort_keys=True)
            f.write("\n")


def test_forwardtest_list_with_outcomes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The list command with --with-outcomes should include outcome status and realised metrics."""
    _setup_runs_and_outcomes(tmp_path)
    # Change working directory to the temporary path
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    # Capture CLI output
    result = runner.invoke(
        cli,
        [
            "forwardtest",
            "list",
            "--start",
            "2026-04-01",
            "--end",
            "2026-04-04",
            "--with-outcomes",
        ],
    )
    assert result.exit_code == 0, result.output
    # Split output into lines and discard empty last line
    lines = [line for line in result.output.strip().split("\n") if line]
    # First line should be header with new columns
    header = lines[0]
    assert header == "ny_date\trun_id\tstatus\tsymbol\toutcome_logged\tpnl_usd\tr\tentry_source"
    # Build mapping from run_id to row values (split by tab)
    rows = {line.split("\t")[1]: line.split("\t") for line in lines[1:]}  # skip header
    # Check Run A metrics (TRADE with outcome entry present)
    a = rows.get("runA")
    assert a is not None
    # Columns: ny_date, run_id, status, symbol, outcome_logged, pnl_usd, r, entry_source
    assert a[0] == "2026-04-01" and a[1] == "runA" and a[2] == "TRADE" and a[3] == "AAA"
    assert a[4] == "Y", "Outcome should be logged for runA"
    # PnL: (exit 110 - entry 101) * shares 2 = 18
    assert abs(float(a[5]) - 18.0) < 1e-6
    # R: (exit-entry)/(entry-stop) = (110-101)/(101-95) = 9/6 = 1.5
    assert abs(float(a[6]) - 1.5) < 1e-6
    assert a[7] == "outcome"
    # Check Run B metrics (TRADE with outcome missing entry)
    b = rows.get("runB")
    assert b is not None
    assert b[0] == "2026-04-02" and b[1] == "runB" and b[2] == "TRADE" and b[3] == "BBB"
    assert b[4] == "Y", "Outcome should be logged for runB"
    # PnL: (exit 55 - entry 50) * shares 4 = 20
    assert abs(float(b[5]) - 20.0) < 1e-6
    # R: (55-50)/(50-45) = 5/5 = 1.0
    assert abs(float(b[6]) - 1.0) < 1e-6
    # entry_source should indicate fallback to decision
    assert b[7] == "decision"
    # Check Run C metrics (TRADE without outcome)
    c = rows.get("runC")
    assert c is not None
    assert c[0] == "2026-04-03" and c[1] == "runC" and c[2] == "TRADE" and c[3] == "CCC"
    assert c[4] == "N", "No outcome should be logged for runC"
    assert c[5] == "-" and c[6] == "-" and c[7] == "-"
    # Check Run D metrics (NO_TRADE)
    d = rows.get("runD")
    assert d is not None
    assert d[0] == "2026-04-04" and d[1] == "runD" and d[2] == "NO_TRADE"
    # symbol may be '-' for None
    assert d[3] == "-" or d[3] == "None"
    # Non-trade rows should have '-' in outcome columns
    assert d[4] == "-" and d[5] == "-" and d[6] == "-" and d[7] == "-"


def test_forwardtest_list_only_missing_outcomes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The --only-missing-outcomes flag should filter to TRADE runs lacking outcomes."""
    _setup_runs_and_outcomes(tmp_path)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "forwardtest",
            "list",
            "--start",
            "2026-04-01",
            "--end",
            "2026-04-04",
            "--with-outcomes",
            "--only-missing-outcomes",
        ],
    )
    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.strip().split("\n") if line]
    # Should include header + only runC (missing outcome) and exclude non-TRADE
    assert len(lines) == 2
    header, row = lines[0], lines[1]
    assert header == "ny_date\trun_id\tstatus\tsymbol\toutcome_logged\tpnl_usd\tr\tentry_source"
    cols = row.split("\t")
    # RunC should be the only entry
    assert cols[1] == "runC"
    assert cols[4] == "N"  # outcome missing
    # Ensure metrics columns are '-' for missing outcome
    assert cols[5] == "-" and cols[6] == "-" and cols[7] == "-"