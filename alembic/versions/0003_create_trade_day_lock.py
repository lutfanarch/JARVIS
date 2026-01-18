"""Create trade day lock table for one-trade-per-day enforcement.

Revision ID: 0003_create_trade_day_lock
Revises: 0002_add_corporate_actions_unique_index
Create Date: 2026-01-18 00:00:00.000000

This migration introduces a new table ``trade_day_lock`` used to
enforce the "one trade per America/New_York trading date" policy.
The table records the NY trading date for which a trade has been
executed along with metadata such as the UTC timestamp of the lock,
the originating run ID, a hash of the decision payload and the
associated symbol.  A unique constraint on ``ny_trading_date``
ensures that only one row can exist per date.  This migration is
compatible with both PostgreSQL and SQLite.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0003_create_trade_day_lock"
down_revision = "0002_add_corporate_actions_unique_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the ``trade_day_lock`` table.

    The table uses ``ny_trading_date`` as the primary key (and hence
    unique) to ensure that only a single trade may be recorded for a
    given NY trading date.  All timestamps are stored with timezone
    information to preserve UTC context.  The ``symbol`` column is
    nullable because no symbol is recorded for vetoed trades.
    """
    op.create_table(
        "trade_day_lock",
        sa.Column("ny_trading_date", sa.Date(), nullable=False),
        sa.Column(
            "locked_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("decision_hash", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("ny_trading_date", name="pk_trade_day_lock"),
    )


def downgrade() -> None:
    """Drop the ``trade_day_lock`` table."""
    op.drop_table("trade_day_lock")