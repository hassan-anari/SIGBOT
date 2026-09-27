"""Add per-destination canonical-symbol trade locks.

Revision ID: 20260814_01
Revises: 20260813_02
"""
from alembic import op
import sqlalchemy as sa

revision = "20260814_01"
down_revision = "20260813_02"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "destination_trade_locks",
        sa.Column("destination_id", sa.Integer(), nullable=False),
        sa.Column("canonical_symbol", sa.Text(), nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["destination_id"], ["destinations.id"], ondelete="CASCADE",
                                name="fk_destination_trade_locks_destination"),
        sa.ForeignKeyConstraint(["signal_id"], ["signals.id"], ondelete="CASCADE",
                                name="fk_destination_trade_locks_signal"),
        sa.CheckConstraint("trim(canonical_symbol) <> ''",
                           name="ck_destination_trade_locks_symbol_nonempty"),
        sa.PrimaryKeyConstraint("destination_id", "canonical_symbol",
                                name="pk_destination_trade_locks"),
    )
    op.create_index("ix_destination_trade_locks_signal_id", "destination_trade_locks",
                    ["signal_id"])


def downgrade():
    op.drop_index("ix_destination_trade_locks_signal_id",
                  table_name="destination_trade_locks")
    op.drop_table("destination_trade_locks")
