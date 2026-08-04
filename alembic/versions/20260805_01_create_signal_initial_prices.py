"""Persist immutable per-exchange initial prices.

Revision ID: 20260805_01
Revises: 20260804_01
"""
from alembic import op
import sqlalchemy as sa
revision="20260805_01";down_revision="20260804_01";branch_labels=None;depends_on=None
def upgrade():
    op.create_table("signal_initial_prices",
        sa.Column("signal_id",sa.Integer(),nullable=False),
        sa.Column("exchange",sa.String(6),nullable=False),
        sa.Column("price",sa.Text(),nullable=False),
        sa.PrimaryKeyConstraint("signal_id","exchange",name="pk_signal_initial_prices"),
        sa.ForeignKeyConstraint(["signal_id"],["signals.id"],ondelete="CASCADE",name="fk_signal_initial_prices_signal_id_signals"),
        sa.ForeignKeyConstraint(["signal_id","exchange"],["signal_exchange_symbols.signal_id","signal_exchange_symbols.exchange"],ondelete="CASCADE",name="fk_signal_initial_prices_signal_exchange_symbols"),
        sa.CheckConstraint("exchange IN ('ourbit','toobit','lbank')",name="signal_initial_price_exchange"),
        sa.CheckConstraint("trim(price) <> ''",name="ck_signal_initial_prices_price_nonempty"),
        sa.CheckConstraint("CAST(price AS NUMERIC) > 0",name="ck_signal_initial_prices_price_positive"))
def downgrade():op.drop_table("signal_initial_prices")
