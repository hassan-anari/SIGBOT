"""Phase 7.4 durable signal cancellation and cancellation text outbox."""
from alembic import op
import sqlalchemy as sa

revision="20260809_04";down_revision="20260809_03";branch_labels=None;depends_on=None

_PUBLICATION="status IN ('pending','rendering','ready','sending','retry_wait','sent','failed','skipped_inactive','skipped_unassigned','skipped_daily_limit','skipped_signallist_inactive','skipped_destination_inactive','skipped_no_initial_message','skipped_no_event_source','superseded','delivery_unknown','blocked_template')"

def upgrade():
    with op.batch_alter_table("signals",recreate="always") as batch:
        batch.drop_constraint("signal_lifecycle_status",type_="check")
        batch.create_check_constraint("signal_lifecycle_status","status IN ('pending_details','active','cancelled')")
        batch.add_column(sa.Column("cancelled_at",sa.DateTime(),nullable=True))
        batch.create_check_constraint("ck_signals_cancelled_at","(status = 'cancelled' AND cancelled_at IS NOT NULL) OR (status <> 'cancelled' AND cancelled_at IS NULL)")
    # 7.1's handwritten CHECK omitted the ORM's already-existing SUPERSEDED value.
    with op.batch_alter_table("signal_details_publications",recreate="always") as batch:
        batch.drop_constraint("destination_publication_status",type_="check")
        batch.create_check_constraint("destination_publication_status",_PUBLICATION)
    with op.batch_alter_table("initial_publication_requests",recreate="always") as batch:
        batch.alter_column("status",existing_type=sa.String(9),type_=sa.String(20),nullable=False)
        batch.drop_constraint("initial_publication_request_status",type_="check")
        batch.create_check_constraint("initial_publication_request_status","status IN ('pending','planning','planned','completed','superseded','failed')")
    with op.batch_alter_table("destination_publications",recreate="always") as batch:
        batch.drop_constraint("destination_publication_status",type_="check")
        batch.create_check_constraint("destination_publication_status",_PUBLICATION)
    op.create_table("signal_cancellation_publications",
      sa.Column("id",sa.Integer(),primary_key=True,autoincrement=True),sa.Column("signal_id",sa.Integer(),nullable=False),sa.Column("destination_id",sa.Integer(),nullable=False),
      sa.Column("status",sa.String(32),nullable=False),sa.Column("telegram_message_id",sa.BigInteger()),sa.Column("reply_to_message_id",sa.BigInteger()),
      sa.Column("attempt_count",sa.Integer(),nullable=False,server_default="0"),sa.Column("retry_at",sa.DateTime()),sa.Column("first_attempt_at",sa.DateTime()),sa.Column("sent_at",sa.DateTime()),sa.Column("last_error_category",sa.Text()),sa.Column("created_at",sa.DateTime(),nullable=False),sa.Column("updated_at",sa.DateTime(),nullable=False),
      sa.ForeignKeyConstraint(["signal_id"],["signals.id"],ondelete="CASCADE"),sa.ForeignKeyConstraint(["destination_id"],["destinations.id"],ondelete="CASCADE"),
      sa.UniqueConstraint("signal_id","destination_id",name="uq_signal_cancellation_destination"),sa.CheckConstraint(_PUBLICATION,name="destination_publication_status"),sa.CheckConstraint("attempt_count >= 0",name="ck_signal_cancellation_attempts"),sa.CheckConstraint("telegram_message_id IS NULL OR telegram_message_id > 0",name="ck_signal_cancellation_message"),sa.CheckConstraint("reply_to_message_id IS NULL OR reply_to_message_id > 0",name="ck_signal_cancellation_reply"))
    op.create_index("ix_signal_cancellation_status_retry","signal_cancellation_publications",["status","retry_at"])

def downgrade():
    op.drop_index("ix_signal_cancellation_status_retry",table_name="signal_cancellation_publications");op.drop_table("signal_cancellation_publications")
    with op.batch_alter_table("signals",recreate="always") as batch:
        batch.drop_constraint("ck_signals_cancelled_at",type_="check");batch.drop_column("cancelled_at");batch.drop_constraint("signal_lifecycle_status",type_="check");batch.create_check_constraint("signal_lifecycle_status","status IN ('pending_details','active')")
