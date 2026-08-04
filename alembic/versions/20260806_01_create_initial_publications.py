"""Create the durable Phase 5C initial-publication outbox.

Revision ID: 20260806_01
Revises: 20260805_01
"""
from alembic import op
import sqlalchemy as sa
revision="20260806_01";down_revision="20260805_01";branch_labels=None;depends_on=None
REQ="pending','planning','planned','completed','failed"
PUB="pending','rendering','ready','sending','retry_wait','sent','failed','skipped_inactive','skipped_unassigned','skipped_daily_limit','delivery_unknown','blocked_template"
def upgrade():
 op.create_table("initial_publication_requests",
  sa.Column("id",sa.Integer(),nullable=False),sa.Column("signal_id",sa.Integer(),nullable=False),
  sa.Column("status",sa.String(9),nullable=False),sa.Column("created_at",sa.DateTime(),nullable=False),
  sa.Column("updated_at",sa.DateTime(),nullable=False),sa.Column("planned_at",sa.DateTime()),sa.Column("completed_at",sa.DateTime()),
  sa.PrimaryKeyConstraint("id",name="pk_initial_publication_requests"),
  sa.ForeignKeyConstraint(["signal_id"],["signals.id"],ondelete="CASCADE",name="fk_initial_publication_requests_signal_id_signals"),
  sa.UniqueConstraint("signal_id",name="uq_initial_publication_requests_signal_id"),
  sa.CheckConstraint(f"status IN ('{REQ}')",name="initial_publication_request_status"))
 op.create_index("ix_initial_publication_requests_status_created","initial_publication_requests",["status","created_at"])
 op.create_table("destination_publications",
  sa.Column("id",sa.Integer(),nullable=False),sa.Column("signal_id",sa.Integer(),nullable=False),sa.Column("destination_id",sa.Integer(),nullable=False),
  sa.Column("event_kind",sa.Text(),nullable=False),sa.Column("selected_exchange",sa.String(6),nullable=False),sa.Column("status",sa.String(18),nullable=False),
  sa.Column("telegram_message_id",sa.BigInteger()),sa.Column("reply_to_message_id",sa.BigInteger()),sa.Column("attempt_count",sa.Integer(),nullable=False),
  sa.Column("last_error_category",sa.Text()),sa.Column("template_sha256",sa.Text()),sa.Column("created_at",sa.DateTime(),nullable=False),
  sa.Column("updated_at",sa.DateTime(),nullable=False),sa.Column("first_attempt_at",sa.DateTime()),sa.Column("retry_at",sa.DateTime()),sa.Column("sent_at",sa.DateTime()),
  sa.PrimaryKeyConstraint("id",name="pk_destination_publications"),
  sa.ForeignKeyConstraint(["signal_id"],["signals.id"],ondelete="CASCADE",name="fk_destination_publications_signal_id_signals"),
  sa.ForeignKeyConstraint(["destination_id"],["destinations.id"],ondelete="CASCADE",name="fk_destination_publications_destination_id_destinations"),
  sa.UniqueConstraint("signal_id","destination_id","event_kind",name="uq_destination_publications_signal_destination_event"),
  sa.CheckConstraint("event_kind = 'initial'",name="ck_destination_publications_event_initial"),
  sa.CheckConstraint("selected_exchange IN ('ourbit','toobit','lbank')",name="ck_destination_publications_exchange"),
  sa.CheckConstraint(f"status IN ('{PUB}')",name="destination_publication_status"),
  sa.CheckConstraint("attempt_count >= 0",name="ck_destination_publications_attempt_nonnegative"),
  sa.CheckConstraint("telegram_message_id IS NULL OR telegram_message_id > 0",name="ck_destination_publications_message_positive"),
  sa.CheckConstraint("reply_to_message_id IS NULL",name="ck_destination_publications_initial_no_reply"),
  sa.CheckConstraint("(status = 'sent' AND telegram_message_id IS NOT NULL AND sent_at IS NOT NULL) OR (status <> 'sent' AND telegram_message_id IS NULL AND sent_at IS NULL)",name="ck_destination_publications_sent_consistency"))
 op.create_index("ix_destination_publications_status_updated","destination_publications",["status","updated_at"])
 op.create_table("initial_publication_daily_slots",
  sa.Column("id",sa.Integer(),nullable=False),sa.Column("destination_id",sa.Integer(),nullable=False),sa.Column("local_date",sa.Date(),nullable=False),
  sa.Column("slot_number",sa.Integer(),nullable=False),sa.Column("publication_id",sa.Integer(),nullable=False),sa.Column("created_at",sa.DateTime(),nullable=False),
  sa.PrimaryKeyConstraint("id",name="pk_initial_publication_daily_slots"),
  sa.ForeignKeyConstraint(["destination_id"],["destinations.id"],ondelete="CASCADE",name="fk_initial_publication_daily_slots_destination_id_destinations"),
  sa.ForeignKeyConstraint(["publication_id"],["destination_publications.id"],ondelete="CASCADE",name="fk_initial_publication_daily_slots_publication_id_publications"),
  sa.UniqueConstraint("destination_id","local_date","slot_number",name="uq_initial_publication_daily_slots_destination_date_slot"),
  sa.UniqueConstraint("publication_id",name="uq_initial_publication_daily_slots_publication_id"),sa.CheckConstraint("slot_number > 0",name="ck_initial_publication_daily_slots_slot_positive"))
 op.create_index("ix_initial_publication_daily_slots_destination_date","initial_publication_daily_slots",["destination_id","local_date"])
def downgrade():
 op.drop_index("ix_initial_publication_daily_slots_destination_date",table_name="initial_publication_daily_slots");op.drop_table("initial_publication_daily_slots")
 op.drop_index("ix_destination_publications_status_updated",table_name="destination_publications");op.drop_table("destination_publications")
 op.drop_index("ix_initial_publication_requests_status_created",table_name="initial_publication_requests");op.drop_table("initial_publication_requests")
