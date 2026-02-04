"""Health check routines for the Informer project.

This module defines a set of deterministic checks that validate
runtime prerequisites, dependency availability, database
configuration, and filesystem writability.  The results are
assembled into a :class:`HealthReport` using the models defined
in :mod:`informer.health.models`.
"""

from __future__ import annotations

import os
import sys
import importlib.metadata
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Any, Iterable, Optional

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy import inspect

from .models import CheckResult, HealthReport, CheckSeverity
from ..ingestion.bars import bars_table
from ..features.storage import features_snapshot_table
from ..quality.storage import data_quality_events_table
from ..ingestion.corporate_actions import corporate_actions_table


def _get_library_version(pkg: str) -> Optional[str]:
    """Return the installed version of a package, if available.

    This helper uses importlib.metadata.version and silently
    ignores missing packages by returning ``None`` rather than
    raising ``PackageNotFoundError``.
    """
    try:
        return importlib.metadata.version(pkg)
    except Exception:
        return None


def _python_version_check() -> CheckResult:
    """Check that the running Python interpreter meets the minimum version requirement."""
    version_info = sys.version_info
    if version_info < (3, 11):
        return CheckResult(
            name="PYTHON_VERSION",
            severity="ERROR",
            message=f"Python {version_info.major}.{version_info.minor} is not supported (requires >=3.11)",
            details={"detected": f"{version_info.major}.{version_info.minor}.{version_info.micro}"},
        )
    return CheckResult(
        name="PYTHON_VERSION",
        severity="INFO",
        message=f"Python {version_info.major}.{version_info.minor}.{version_info.micro} detected",
    )


def _dependencies_check() -> CheckResult:
    """Verify that required dependencies are importable.

    Returns an ERROR check if any mandatory dependencies are missing; otherwise
    INFO with a summary of versions.
    """
    required = [
        "pandas",
        "numpy",
        "sqlalchemy",
        "requests",
        "click",
        "pydantic",
        "mplfinance",
        "matplotlib",
    ]
    missing: List[str] = []
    versions: Dict[str, str] = {}
    for pkg in required:
        try:
            module = __import__(pkg)
            # Attempt to get version if available on module
            ver = getattr(module, "__version__", None)
            if ver:
                versions[pkg] = ver
        except Exception:
            missing.append(pkg)
    if missing:
        return CheckResult(
            name="DEPENDENCIES_PRESENT",
            severity="ERROR",
            message=f"Missing required dependencies: {', '.join(sorted(missing))}",
            details={"missing": sorted(missing)},
        )
    return CheckResult(
        name="DEPENDENCIES_PRESENT",
        severity="INFO",
        message="All required dependencies are installed",
        details={"versions": versions},
    )


def _talib_optional_check() -> CheckResult:
    """Check whether TA‑Lib is installed; always informational."""
    installed = True
    try:
        __import__("talib")
    except Exception:
        installed = False
    return CheckResult(
        name="TALIB_OPTIONAL",
        severity="INFO",
        message=f"TA-Lib installed: {installed}",
    )


def _env_database_check() -> CheckResult:
    """Check presence of DATABASE_URL environment variable."""
    url = os.getenv("DATABASE_URL")
    if not url:
        return CheckResult(
            name="ENV_DATABASE_URL",
            severity="ERROR",
            message="DATABASE_URL environment variable is not set",
        )
    return CheckResult(
        name="ENV_DATABASE_URL",
        severity="INFO",
        message="DATABASE_URL environment variable present",
    )


def _env_alpaca_keys_check() -> CheckResult:
    """Check presence of Alpaca API keys; warn if missing."""
    missing = []
    if not os.getenv("ALPACA_API_KEY_ID"):
        missing.append("ALPACA_API_KEY_ID")
    if not os.getenv("ALPACA_API_SECRET_KEY"):
        missing.append("ALPACA_API_SECRET_KEY")
    if missing:
        return CheckResult(
            name="ENV_ALPACA_KEYS",
            severity="WARN",
            message=f"Missing Alpaca API keys: {', '.join(missing)}",
        )
    return CheckResult(
        name="ENV_ALPACA_KEYS",
        severity="INFO",
        message="Alpaca API keys present",
    )


def _db_connect_check(engine: Engine) -> CheckResult:
    """Check database connectivity by executing a simple SELECT 1."""
    try:
        with engine.connect() as conn:
            result = conn.execute(sa.text("SELECT 1")).scalar()
        # If result is not 1, something is wrong
        if result != 1:
            raise RuntimeError(f"Unexpected result {result}")
    except Exception as e:
        return CheckResult(
            name="DB_CONNECT",
            severity="ERROR",
            message="Cannot connect to database",
            details={"error": str(e)},
        )
    return CheckResult(
        name="DB_CONNECT",
        severity="INFO",
        message="Database connection OK",
    )


def _db_schema_check(engine: Engine) -> CheckResult:
    """Verify that required tables exist in the database."""
    required_tables = {
        "bars",
        "features_snapshot",
        "data_quality_events",
        "corporate_actions",
        "alembic_version",
    }
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
    except Exception as e:
        return CheckResult(
            name="DB_SCHEMA_TABLES",
            severity="ERROR",
            message="Failed to inspect database tables",
            details={"error": str(e)},
        )
    missing = sorted(required_tables - tables)
    if missing:
        return CheckResult(
            name="DB_SCHEMA_TABLES",
            severity="ERROR",
            message=f"Missing required tables: {', '.join(missing)}",
            details={"missing": missing},
        )
    return CheckResult(
        name="DB_SCHEMA_TABLES",
        severity="INFO",
        message="All required tables present",
    )


def _artifacts_writable_check(artifacts_root: Path) -> CheckResult:
    """Ensure that charts, packets and health directories are writable under the artifacts root."""
    try:
        for sub in ["charts", "packets", "health"]:
            dir_path = artifacts_root / sub
            dir_path.mkdir(parents=True, exist_ok=True)
            # Write a small temporary file and remove it
            test_file = dir_path / "_write_test.tmp"
            with test_file.open("w", encoding="utf-8") as f:
                f.write("test")
            test_file.unlink()
    except Exception as e:
        return CheckResult(
            name="ARTIFACTS_WRITABLE",
            severity="ERROR",
            message="Artifacts directory is not writable",
            details={"error": str(e)},
        )
    return CheckResult(
        name="ARTIFACTS_WRITABLE",
        severity="INFO",
        message="Artifacts directories are writable",
    )



def _whitelist_enforced_check(symbols: Iterable[str]) -> CheckResult:
    """Ensure that the requested symbols are within the allowed whitelist.

    The allowed whitelist is defined by the environment variable ``SYMBOLS``
    if provided, otherwise by the canonical whitelist from :mod:`informer.config`.
    When an environment-defined whitelist contains symbols not present in the
    canonical list, this function returns an ERROR result immediately.
    Subsequently, any requested symbols outside of the resolved whitelist
    will also trigger an ERROR.
    """
    from informer.config import CANONICAL_WHITELIST

    env_syms = os.getenv("SYMBOLS")
    if env_syms:
        # Use environment-defined whitelist (uppercased and stripped)
        whitelist = [s.strip().upper() for s in env_syms.split(",") if s.strip()]
        # Validate that env-provided symbols are all within the canonical list
        invalid_env = [s for s in whitelist if s not in CANONICAL_WHITELIST]
        if invalid_env:
            return CheckResult(
                name="WHITELIST_ENFORCED",
                severity="ERROR",
                message=f"Environment SYMBOLS contains invalid symbols: {', '.join(sorted(invalid_env))}",
                details={"invalid_env": sorted(invalid_env), "canonical": CANONICAL_WHITELIST},
            )
    else:
        # No env override; use canonical list
        whitelist = list(CANONICAL_WHITELIST)
    # Determine if requested symbols are outside of resolved whitelist
    extra = [s for s in symbols if s not in whitelist]
    if extra:
        return CheckResult(
            name="WHITELIST_ENFORCED",
            severity="ERROR",
            message=f"Symbols not allowed by whitelist: {', '.join(sorted(extra))}",
            details={"extra": sorted(extra), "whitelist": whitelist},
        )
    return CheckResult(
        name="WHITELIST_ENFORCED",
        severity="INFO",
        message="All symbols are within the allowed whitelist",
    )

def build_health_report(
    *,
    engine: Engine | None,
    run_id: str,
    schema_version: str,
    feature_version: str,
    chart_version: str,
    provider_version: str,
    artifacts_root: Path,
    symbols: List[str],
    timeframes: List[str],
    strict: bool = False,
) -> HealthReport:
    """Run a suite of health checks and return a summary report.

    Parameters
    ----------
    engine : sqlalchemy.engine.Engine
        Database engine used for connection and schema checks.
    run_id : str
        Unique identifier for this healthcheck run.
    schema_version : str
        Current schema version used in the system.
    feature_version : str
        Current feature version used for computed indicators.
    chart_version : str
        Current chart version used for chart rendering.
    provider_version : str
        Version tag for the data provider (e.g. Alpaca REST API).
    artifacts_root : pathlib.Path
        Root directory for artifacts (charts, packets, health reports).
    symbols : list of str
        Symbols to validate against the whitelist.
    timeframes : list of str
        Timeframes to include in environment snapshot (unused in checks but reported).
    strict : bool, optional
        Unused in the report itself.  Strict mode controls the CLI exit code
        (see :mod:`informer.cli`) but does not alter the report status.

    Returns
    -------
    HealthReport
        A populated health report summarising all checks.
    """
    # Prepare list of check results
    checks: List[CheckResult] = []
    # Base checks independent of database
    checks.append(_python_version_check())
    checks.append(_dependencies_check())
    checks.append(_talib_optional_check())
    checks.append(_env_database_check())
    checks.append(_env_alpaca_keys_check())
    # Database checks only if an engine is provided
    if engine is None:
        # Without an engine we cannot connect or inspect schema
        checks.append(
            CheckResult(
                name="DB_CONNECT",
                severity="ERROR",
                message="Database engine unavailable",
                details={},
            )
        )
        checks.append(
            CheckResult(
                name="DB_SCHEMA_TABLES",
                severity="ERROR",
                message="Cannot inspect schema without a database connection",
                details={},
            )
        )
    else:
        checks.append(_db_connect_check(engine))
        checks.append(_db_schema_check(engine))
    # Filesystem and whitelist checks
    checks.append(_artifacts_writable_check(artifacts_root))
    checks.append(_whitelist_enforced_check(symbols))
    # Determine status: OK only if there are no ERROR checks
    status = "OK"
    for check in checks:
        if check.severity == "ERROR":
            status = "NOT_READY"
            break
    # Build versions dictionary.  Combine passed versions and library versions.
    versions: Dict[str, str] = {
        "schema_version": schema_version,
        "feature_version": feature_version,
        "chart_version": chart_version,
        "provider_version": provider_version,
    }
    # Add key library versions if available
    for pkg in [
        "pandas",
        "numpy",
        "sqlalchemy",
        "pydantic",
        "mplfinance",
        "matplotlib",
        "pytest",
    ]:
        ver = _get_library_version(pkg)
        if ver:
            versions[pkg] = ver
    # Prepare environment summary
    # Sanitize by not including secret values
    env_summary: Dict[str, Any] = {}
    env_summary["symbols"] = symbols
    env_summary["timeframes"] = timeframes
    env_summary["database_url_present"] = bool(os.getenv("DATABASE_URL"))
    env_summary["alpaca_keys_present"] = bool(os.getenv("ALPACA_API_KEY_ID") and os.getenv("ALPACA_API_SECRET_KEY"))
    env_summary["artifacts_root"] = str(artifacts_root)
    # Generate timestamp (tz-aware UTC, no microseconds)
    generated_at = datetime.now(timezone.utc).replace(microsecond=0)
    # Sort checks by name for deterministic output
    checks_sorted = sorted(checks, key=lambda c: c.name)
    report = HealthReport(
        run_id=run_id,
        generated_at=generated_at,
        status=status,
        checks=checks_sorted,
        versions=versions,
        environment=env_summary,
    )
    return report