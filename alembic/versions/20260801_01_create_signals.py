"""Create normalized Phase 5A signals.

Revision ID: 20260801_01
Revises: 20260731_02
"""
from alembic import op
import sqlalchemy as sa
revision="20260801_01"; down_revision="20260731_02"; branch_labels=None; depends_on=None
UTC_DEFAULT=sa.text("(strftime('%Y-%m-%dT%H:%M:%f000+00:00', 'now'))")
def upgrade():
    op.create_table("signals",
      sa.Column("id",sa.Integer(),autoincrement=True,nullable=False), sa.Column("signalist_id",sa.Integer(),nullable=False),
      sa.Column("creation_token",sa.Text(),nullable=False), sa.Column("mode",sa.String(15),nullable=False),
      sa.Column("canonical_symbol",sa.Text(),nullable=False),sa.Column("base_asset",sa.Text(),nullable=False),sa.Column("quote_asset",sa.Text(),nullable=False),
      sa.Column("selected_exchange",sa.String(6),nullable=True),sa.Column("direction",sa.String(5),nullable=False),
      sa.Column("entry_price",sa.Text(),nullable=False),sa.Column("leverage",sa.Integer(),nullable=False),sa.Column("stop_loss",sa.Text(),nullable=False),
      sa.Column("reference_price",sa.Text(),nullable=False),sa.Column("price_source_type",sa.String(11),nullable=False),sa.Column("status",sa.String(6),nullable=False),
      sa.Column("created_at",sa.Text(),server_default=UTC_DEFAULT,nullable=False),sa.Column("updated_at",sa.Text(),server_default=UTC_DEFAULT,nullable=False),
      sa.PrimaryKeyConstraint("id",name="pk_signals"),sa.ForeignKeyConstraint(["signalist_id"],["signalists.id"],name="fk_signals_signalist_id_signalists"),
      sa.UniqueConstraint("creation_token",name="uq_signals_creation_token"),sa.CheckConstraint("leverage >= 1",name="ck_signals_leverage_positive"),
      sa.CheckConstraint("trim(canonical_symbol) <> ''",name="ck_signals_symbol_nonempty"),
      sa.CheckConstraint("mode IN ('composite','single_exchange')",name="signal_mode"),sa.CheckConstraint("direction IN ('long','short')",name="trade_direction"),
      sa.CheckConstraint("selected_exchange IS NULL OR selected_exchange IN ('ourbit','toobit','lbank')",name="destination_exchange"),
      sa.CheckConstraint("price_source_type IN ('composite','ourbit_only','toobit_only','lbank_only')",name="signal_price_source_type"),
      sa.CheckConstraint("status IN ('active')",name="signal_lifecycle_status"))
    op.create_table("signal_take_profits",sa.Column("signal_id",sa.Integer(),nullable=False),sa.Column("position",sa.Integer(),nullable=False),sa.Column("price",sa.Text(),nullable=False),
      sa.PrimaryKeyConstraint("signal_id","position",name="pk_signal_take_profits"),sa.ForeignKeyConstraint(["signal_id"],["signals.id"],ondelete="CASCADE",name="fk_signal_take_profits_signal_id_signals"),
      sa.UniqueConstraint("signal_id","position",name="uq_signal_take_profits_signal_position"),sa.CheckConstraint("position >= 1",name="ck_signal_take_profits_position_positive"))
    op.create_table("signal_exchange_symbols",sa.Column("signal_id",sa.Integer(),nullable=False),sa.Column("exchange",sa.String(6),nullable=False),sa.Column("native_symbol",sa.Text(),nullable=False),
      sa.PrimaryKeyConstraint("signal_id","exchange",name="pk_signal_exchange_symbols"),sa.ForeignKeyConstraint(["signal_id"],["signals.id"],ondelete="CASCADE",name="fk_signal_exchange_symbols_signal_id_signals"),
      sa.CheckConstraint("exchange IN ('ourbit','toobit','lbank')",name="signal_exchange"))
def downgrade():
    op.drop_table("signal_exchange_symbols"); op.drop_table("signal_take_profits"); op.drop_table("signals")
