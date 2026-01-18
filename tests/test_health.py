"""Unit tests for the Informer health check subsystem."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa

from informer.health.checks import build_health_report
from informer.health.models import HealthReport
from informer.ingestion.bars import bars_table, metadata as bars_metadata
from informer.features.storage import features_snapshot_table, metadata as features_metadata
from informer.quality.storage import data_quality_events_table, metadata as quality_metadata
from informer.ingestion.corporate_actions import corporate_actions_table, metadata as ca_metadata


def _create_alembic_version_table(engine: sa.engine.Engine) -> None:
    """Create a minimal alembic_version table for tests."""
    # Directly create an alembic_version table with one column
    from sqlalchemy import Table, Column, MetaData, Text
    metadata = MetaData()
    version_table = Table("alembic_version", metadata, Column("version_num", Text, nullable=False))
    metadata.create_all(engine)


def test_health_report_ok(monkeypatch, tmp_path) -> None:
    """Health report should be OK when environment, dependencies and schema are satisfied."""
    engine = sa.create_engine("sqlite:///:memory:")
    # Create required tables
    bars_metadata.create_all(engine, tables=[bars_table])
    features_metadata.create_all(engine, tables=[features_snapshot_table])
    quality_metadata.create_all(engine, tables=[data_quality_events_table])
    ca_metadata.create_all(engine, tables=[corporate_actions_table])
    _create_alembic_version_table(engine)
    # Set environment variables for DB and clear Alpaca keys to test WARN
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
    report = build_health_report(
        engine=engine,
        run_id="testrun",
        schema_version="v0.1",
        feature_version="v0.1",
        chart_version="v0.1",
        provider_version="test-provider",
        artifacts_root=tmp_path,
        symbols=["AAPL"],
        timeframes=["15m", "1h"],
        strict=False,
    )
    assert isinstance(report, HealthReport)
    assert report.status == "OK"
    # All checks must be present
    check_names = {chk.name for chk in report.checks}
    expected_names = {
        "PYTHON_VERSION",
        "DEPENDENCIES_PRESENT",
        "TALIB_OPTIONAL",
        "ENV_DATABASE_URL",
        "ENV_ALPACA_KEYS",
        "DB_CONNECT",
        "DB_SCHEMA_TABLES",
        "ARTIFACTS_WRITABLE",
        "WHITELIST_ENFORCED",
    }
    assert check_names == expected_names
    # No check should have severity ERROR
    assert all(chk.severity != "ERROR" for chk in report.checks)


def test_health_report_missing_table(tmp_path) -> None:
    """Missing required tables should result in NOT_READY status and an error."""
    engine = sa.create_engine("sqlite:///:memory:")
    # Create only bars table and alembic_version
    bars_metadata.create_all(engine, tables=[bars_table])
    _create_alembic_version_table(engine)
    report = build_health_report(
        engine=engine,
        run_id="testrun",
        schema_version="v0.1",
        feature_version="v0.1",
        chart_version="v0.1",
        provider_version="test-provider",
        artifacts_root=tmp_path,
        symbols=["AAPL"],
        timeframes=["15m"],
        strict=False,
    )
    assert report.status == "NOT_READY"
    # There should be a DB_SCHEMA_TABLES error
    errors = [chk for chk in report.checks if chk.severity == "ERROR"]
    assert any(chk.name == "DB_SCHEMA_TABLES" for chk in errors)


def test_health_report_engine_none(tmp_path) -> None:
    """When no engine is provided, DB checks should report errors and status is NOT_READY."""
    # No database engine passed (None)
    report = build_health_report(
        engine=None,
        run_id="testrun",
        schema_version="v0.1",
        feature_version="v0.1",
        chart_version="v0.1",
        provider_version="test-provider",
        artifacts_root=tmp_path,
        symbols=["AAPL"],
        timeframes=["15m"],
        strict=False,
    )
    assert report.status == "NOT_READY"
    # Should contain DB_CONNECT and DB_SCHEMA_TABLES errors
    names = [chk.name for chk in report.checks if chk.severity == "ERROR"]
    assert "DB_CONNECT" in names
    assert "DB_SCHEMA_TABLES" in names


def test_health_report_whitelist_enforced(tmp_path) -> None:
    """Symbols outside the whitelist should trigger an error."""
    engine = sa.create_engine("sqlite:///:memory:")
    # Create all required tables for a healthy report
    bars_metadata.create_all(engine, tables=[bars_table])
    features_metadata.create_all(engine, tables=[features_snapshot_table])
    quality_metadata.create_all(engine, tables=[data_quality_events_table])
    ca_metadata.create_all(engine, tables=[corporate_actions_table])
    _create_alembic_version_table(engine)
    # Pass an invalid symbol that is not part of the halal universe
    report = build_health_report(
        engine=engine,
        run_id="testrun",
        schema_version="v0.1",
        feature_version="v0.1",
        chart_version="v0.1",
        provider_version="test-provider",
        artifacts_root=tmp_path,
        symbols=["INVALID"],
        timeframes=["15m"],
        strict=False,
    )
    assert report.status == "NOT_READY"
    errors = [chk for chk in report.checks if chk.severity == "ERROR"]
    assert any(chk.name == "WHITELIST_ENFORCED" for chk in errors)


def test_health_report_whitelist_accepts_tsla(monkeypatch, tmp_path) -> None:
    """A newly whitelisted symbol (e.g. TSLA) should not trigger whitelist errors.

    To avoid nondeterministic failures caused by missing environment
    variables (e.g. DATABASE_URL), this test explicitly sets a
    SQLite in-memory connection string and clears any Alpaca keys from
    the environment.  Without this setup the healthcheck would return
    a NOT_READY status due to the ENV_DATABASE_URL check.
    """
    # Ensure deterministic environment variables
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    # Clear Alpaca API keys to avoid leaking developer values; a WARN is acceptable
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
    engine = sa.create_engine("sqlite:///:memory:")
    # Create all required tables
    bars_metadata.create_all(engine, tables=[bars_table])
    features_metadata.create_all(engine, tables=[features_snapshot_table])
    quality_metadata.create_all(engine, tables=[data_quality_events_table])
    ca_metadata.create_all(engine, tables=[corporate_actions_table])
    _create_alembic_version_table(engine)
    report = build_health_report(
        engine=engine,
        run_id="testrun",
        schema_version="v0.1",
        feature_version="v0.1",
        chart_version="v0.1",
        provider_version="test-provider",
        artifacts_root=tmp_path,
        symbols=["TSLA"],
        timeframes=["15m"],
        strict=False,
    )
    # With a valid symbol and properly configured env vars the report should be OK
    assert report.status == "OK"
    assert all(
        not (chk.name == "WHITELIST_ENFORCED" and chk.severity == "ERROR")
        for chk in report.checks
    )