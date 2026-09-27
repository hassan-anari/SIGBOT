"""Add durable Limit-order lifecycle and outboxes.

Revision ID: 20260818_01
Revises: 20260817_01
"""
from alembic import op
import sqlalchemy as sa

revision="20260818_01";down_revision="20260817_01";branch_labels=None;depends_on=None

_STATUS="status IN ('pending_details','pending_entry','entry_triggered','active','cancelled')"
_KIND="message_kind IN ('initial_photo_caption','signal_details','tp_hit','sl_hit','breakeven','signal_cancelled','warmup','risk_free_activated','save_profit_50','full_close','risk_free_hit','limit_pending','limit_cancelled')"

def upgrade():
    with op.batch_alter_table("signals") as b:
        b.add_column(sa.Column("entry_type",sa.Text(),nullable=False,server_default="market"))
        b.create_check_constraint("ck_signals_entry_type","entry_type IN ('market','limit')")
        b.drop_constraint("signal_lifecycle_status",type_="check")
        b.create_check_constraint("signal_lifecycle_status",_STATUS)
    with op.batch_alter_table("destination_message_templates") as b:
        b.drop_constraint("destination_message_kind",type_="check")
        b.create_check_constraint("destination_message_kind",_KIND)
    op.create_table("limit_entry_triggers",
      sa.Column("signal_id",sa.Integer(),primary_key=True),sa.Column("requested_entry",sa.Text(),nullable=False),
      sa.Column("observed_reference_price",sa.Text(),nullable=False),sa.Column("price_source_type",sa.Text(),nullable=False),
      sa.Column("occurred_at",sa.DateTime(),nullable=False),sa.Column("created_at",sa.DateTime(),nullable=False),
      sa.ForeignKeyConstraint(["signal_id"],["signals.id"],ondelete="CASCADE"),
      sa.CheckConstraint("CAST(requested_entry AS NUMERIC)>0 AND CAST(observed_reference_price AS NUMERIC)>0",name="ck_limit_entry_triggers_prices"))
    op.create_index("ix_limit_entry_triggers_occurred","limit_entry_triggers",["occurred_at"])
    op.create_table("limit_entry_trigger_source_prices",
      sa.Column("signal_id",sa.Integer(),primary_key=True),sa.Column("exchange",sa.Text(),primary_key=True),
      sa.Column("price",sa.Text(),nullable=False),sa.ForeignKeyConstraint(["signal_id"],["limit_entry_triggers.signal_id"],ondelete="CASCADE"))
    op.create_table("limit_pending_publications",
      sa.Column("id",sa.Integer(),primary_key=True),sa.Column("signal_id",sa.Integer(),nullable=False),
      sa.Column("destination_id",sa.Integer(),nullable=False),sa.Column("status",sa.Text(),nullable=False,server_default="pending"),
      sa.Column("telegram_message_id",sa.BigInteger()),sa.Column("attempt_count",sa.Integer(),nullable=False,server_default="0"),
      sa.Column("retry_at",sa.DateTime()),sa.Column("sent_at",sa.DateTime()),sa.Column("last_error_category",sa.Text()),
      sa.Column("created_at",sa.DateTime(),nullable=False),sa.Column("updated_at",sa.DateTime(),nullable=False),
      sa.ForeignKeyConstraint(["signal_id"],["signals.id"],ondelete="CASCADE"),
      sa.ForeignKeyConstraint(["destination_id"],["destinations.id"],ondelete="CASCADE"),
      sa.UniqueConstraint("signal_id","destination_id",name="uq_limit_pending_signal_destination"),
      sa.CheckConstraint("attempt_count>=0",name="ck_limit_pending_attempts"),
      sa.CheckConstraint("telegram_message_id IS NULL OR telegram_message_id>0",name="ck_limit_pending_message"))
    op.create_index("ix_limit_pending_status_retry","limit_pending_publications",["status","retry_at"])
    op.create_table("limit_admission_daily_slots",
      sa.Column("id",sa.Integer(),primary_key=True),sa.Column("destination_id",sa.Integer(),nullable=False),
      sa.Column("local_date",sa.Date(),nullable=False),sa.Column("slot_number",sa.Integer(),nullable=False),
      sa.Column("publication_id",sa.Integer(),nullable=False),sa.Column("created_at",sa.DateTime(),nullable=False),
      sa.ForeignKeyConstraint(["destination_id"],["destinations.id"],ondelete="CASCADE"),
      sa.ForeignKeyConstraint(["publication_id"],["limit_pending_publications.id"],ondelete="CASCADE"),
      sa.UniqueConstraint("destination_id","local_date","slot_number",name="uq_limit_slot_destination_date_number"),
      sa.UniqueConstraint("publication_id",name="uq_limit_slot_publication"),sa.CheckConstraint("slot_number>0",name="ck_limit_slot_positive"))

def downgrade():
    op.drop_table("limit_admission_daily_slots");op.drop_index("ix_limit_pending_status_retry",table_name="limit_pending_publications")
    op.drop_table("limit_pending_publications");op.drop_table("limit_entry_trigger_source_prices")
    op.drop_index("ix_limit_entry_triggers_occurred",table_name="limit_entry_triggers");op.drop_table("limit_entry_triggers")
    with op.batch_alter_table("destination_message_templates") as b:
        b.drop_constraint("destination_message_kind",type_="check")
        b.create_check_constraint("destination_message_kind","message_kind IN ('initial_photo_caption','signal_details','tp_hit','sl_hit','breakeven','signal_cancelled','warmup','risk_free_activated','save_profit_50','full_close','risk_free_hit')")
    with op.batch_alter_table("signals") as b:
        b.drop_constraint("signal_lifecycle_status",type_="check")
        b.create_check_constraint("signal_lifecycle_status","status IN ('pending_details','active','cancelled')")
        b.drop_constraint("ck_signals_entry_type",type_="check");b.drop_column("entry_type")
