"""Initial schema for the Informer database.

Revision ID: 0001_create_schema
Revises: 
Create Date: 2026-01-16

This migration creates the core tables used by the Informer system.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0001_create_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the core database schema.

    This migration supports both PostgreSQL (with TimescaleDB) and SQLite.  On
    PostgreSQL the TimescaleDB extension and hypertable conversions are
    performed; on SQLite (or any other dialect) these statements are
    skipped.  The table structures are identical across dialects so
    higher‑level code can operate uniformly.
    """
    # Determine the current SQLAlchemy dialect
    bind = op.get_bind()
    dialect = bind.dialect.name  # e.g. 'postgresql', 'sqlite'
    is_postgres = dialect == "postgresql"

    # Enable the TimescaleDB extension only on PostgreSQL
    if is_postgres:
        op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    # Create bars table
    op.create_table(
        "bars",
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("timeframe", sa.Text(), nullable=False),
        sa.Column("ts", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.Column("vwap", sa.Float(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column(
            "inserted_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("symbol", "timeframe", "ts", name="pk_bars"),
    )

    # Convert to hypertable on PostgreSQL/TimescaleDB
    if is_postgres:
        op.execute("SELECT create_hypertable('bars', 'ts', if_not_exists => TRUE)")

    # Create data_quality_events table
    op.create_table(
        "data_quality_events",
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("timeframe", sa.Text(), nullable=False),
        sa.Column("ts", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "inserted_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    if is_postgres:
        op.execute(
            "SELECT create_hypertable('data_quality_events', 'ts', if_not_exists => TRUE)"
        )

    # Create corporate_actions table
    op.create_table(
        "corporate_actions",
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("action_type", sa.Text(), nullable=False),
        sa.Column("ex_date", sa.DATE(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column(
            "ingested_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # Create features_snapshot table
    op.create_table(
        "features_snapshot",
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("timeframe", sa.Text(), nullable=False),
        sa.Column("ts", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("indicators_json", sa.JSON(), nullable=False),
        sa.Column("patterns_json", sa.JSON(), nullable=False),
        sa.Column("feature_version", sa.Text(), nullable=False),
        sa.Column(
            "inserted_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint(
            "symbol",
            "timeframe",
            "ts",
            "feature_version",
            name="pk_features_snapshot",
        ),
    )


def downgrade() -> None:
    op.drop_table("features_snapshot")
    op.drop_table("corporate_actions")
    op.drop_table("data_quality_events")
    op.drop_table("bars")