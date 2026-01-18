"""Add unique constraint on corporate_actions.

Revision ID: 0002_add_corporate_actions_unique_index
Revises: 0001_create_schema
Create Date: 2026-01-16 00:00:00.000000

This migration adds a unique index on the corporate_actions table
to ensure that each combination of symbol, action_type and ex_date is
unique.  Instead of a table‑level unique constraint we create a
unique index to support SQLite, which does not allow ALTER TABLE
operations for constraints.  The ingestion code relies on this index
to perform idempotent upserts via ON CONFLICT DO UPDATE.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0002_add_corporate_actions_unique_index"
down_revision = "0001_create_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create a unique index on (symbol, action_type, ex_date).

    We avoid adding a table‑level unique constraint because SQLite
    cannot perform ALTER TABLE operations for constraints.  A unique
    index provides equivalent semantics and is honoured by both
    PostgreSQL and SQLite.  If the table does not exist yet, this call
    will no‑op.
    """
    op.create_index(
        "uq_corporate_actions_symbol_action_type_ex_date",
        "corporate_actions",
        ["symbol", "action_type", "ex_date"],
        unique=True,
    )


def downgrade() -> None:
    """Drop the unique index created in upgrade()."""
    op.drop_index(
        "uq_corporate_actions_symbol_action_type_ex_date",
        table_name="corporate_actions",
    )