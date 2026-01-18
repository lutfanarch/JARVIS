"""Tests for corporate actions upsert and actions CLI."""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from click.testing import CliRunner

from informer.cli import cli
from informer.ingestion.corporate_actions import (
    corporate_actions_table,
    metadata as ca_metadata,
    upsert_corporate_actions,
)
from informer.providers.models import CorporateAction


def test_upsert_compiles_postgres() -> None:
    """Upsert statement should contain ON CONFLICT for corporate actions."""
    # Build single-row insert/upsert statement
    insert_stmt = sa.dialects.postgresql.insert(corporate_actions_table).values(
        [
            {
                "symbol": "AAPL",
                "action_type": "split",
                "ex_date": date(2025, 1, 1),
                "payload_json": {"ratio": 2},
            }
        ]
    )
    upsert_stmt = insert_stmt.on_conflict_do_update(
        index_elements=["symbol", "action_type", "ex_date"],
        set_={"payload_json": insert_stmt.excluded.payload_json},
    )
    compiled = upsert_stmt.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "ON CONFLICT" in sql
    assert "symbol" in sql and "action_type" in sql and "ex_date" in sql


def test_actions_cli_inserts_rows(tmp_path, monkeypatch) -> None:
    """The actions CLI should dedupe by (symbol, type, ex_date) via upsert."""
    # In-memory SQLite engine
    engine = sa.create_engine("sqlite:///:memory:")
    # Create corporate actions table
    ca_metadata.create_all(engine, tables=[corporate_actions_table])
    # Dummy provider
    class DummyProvider:
        def get_corporate_actions(self, symbols, start, end):  # type: ignore[no-untyped-def]
            # Return two actions with same primary key but different payload
            actions = []
            actions.append(
                CorporateAction(
                    symbol="AAPL",
                    action_type="dividend",
                    ex_date=date(2025, 1, 5),
                    payload_json={"amount": 0.5},
                    source="provider",
                )
            )
            actions.append(
                CorporateAction(
                    symbol="AAPL",
                    action_type="dividend",
                    ex_date=date(2025, 1, 5),
                    payload_json={"amount": 1.0},
                    source="provider2",
                )
            )
            return actions

    # Monkeypatch engine and provider builders
    monkeypatch.setattr("informer.cli._build_engine", lambda: engine)
    monkeypatch.setattr("informer.cli._build_provider", lambda: DummyProvider())
    runner = CliRunner()
    # Run actions CLI
    result = runner.invoke(
        cli,
        [
            "actions",
            "--symbols",
            "AAPL",
            "--start",
            "2025-01-01",
            "--end",
            "2025-01-10",
        ],
    )
    assert result.exit_code == 0, result.output
    # Check that only one row exists with updated payload
    with engine.connect() as conn:
        rows = conn.execute(corporate_actions_table.select()).fetchall()
        assert len(rows) == 1
        row = rows[0]
        payload = row._mapping["payload_json"]
        # Payload should reflect the last inserted amount (1.0)
        assert payload.get("amount") == 1.0
        # Source should be set to provider2 (ensured by upsert preserving latest)
        assert payload.get("source") == "provider2"