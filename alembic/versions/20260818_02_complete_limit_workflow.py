"""Complete Limit workflow wiring and unify admission capacity.

Revision ID: 20260818_02
Revises: 20260818_01
"""
from alembic import op
import sqlalchemy as sa

revision="20260818_02"
down_revision="20260818_01"
branch_labels=None
depends_on=None

PUBLICATION_STATUSES="'pending','rendering','ready','sending','retry_wait','sent','failed','skipped_inactive','skipped_unassigned','skipped_daily_limit','skipped_signallist_inactive','skipped_destination_inactive','skipped_no_initial_message','skipped_no_event_source','superseded','delivery_unknown','blocked_template'"

def upgrade():
    connection=op.get_bind()
    # The first PR draft never wired or populated this table. Refuse to silently
    # reinterpret data if a development deployment nevertheless wrote to it.
    if connection.execute(sa.text("SELECT COUNT(*) FROM limit_admission_daily_slots")).scalar_one():
        raise RuntimeError("cannot unify non-empty legacy Limit capacity slots; remove draft data explicitly")
    op.drop_table("limit_admission_daily_slots")
    with op.batch_alter_table("signals") as b:
        b.drop_constraint("ck_signals_entry_type",type_="check")
        b.create_check_constraint("signal_entry_type","entry_type IN ('market','limit')")
    with op.batch_alter_table("limit_entry_triggers") as b:
        b.create_check_constraint("signal_price_source_type",
            "price_source_type IN ('composite','ourbit_only','toobit_only','lbank_only')")
    with op.batch_alter_table("limit_entry_trigger_source_prices") as b:
        b.create_check_constraint("destination_exchange","exchange IN ('ourbit','toobit','lbank')")
    with op.batch_alter_table("limit_pending_publications") as b:
        b.add_column(sa.Column("selected_exchange",sa.Text(),nullable=False,server_default="ourbit"))
        b.create_check_constraint("destination_exchange","selected_exchange IN ('ourbit','toobit','lbank')")
        b.create_check_constraint("limit_pending_publication_status",f"status IN ({PUBLICATION_STATUSES})")
        b.create_check_constraint("ck_limit_pending_sent",
            "(status='sent' AND telegram_message_id IS NOT NULL AND sent_at IS NOT NULL) OR (status<>'sent' AND telegram_message_id IS NULL AND sent_at IS NULL)")
    op.create_table("limit_admission_requests",
        sa.Column("id",sa.Integer(),primary_key=True),sa.Column("signal_id",sa.Integer(),nullable=False),
        sa.Column("status",sa.Text(),nullable=False,server_default="pending"),
        sa.Column("created_at",sa.DateTime(),nullable=False),sa.Column("updated_at",sa.DateTime(),nullable=False),
        sa.Column("planned_at",sa.DateTime()),
        sa.ForeignKeyConstraint(["signal_id"],["signals.id"],ondelete="CASCADE"),
        sa.UniqueConstraint("signal_id",name="uq_limit_admission_request_signal"),
        sa.CheckConstraint("status IN ('pending','planning','planned','superseded')",name="ck_limit_admission_request_status"))
    op.create_index("ix_limit_admission_requests_status","limit_admission_requests",["status","created_at"])
    # Every already-created draft Limit must be planned once; historical Market
    # rows deliberately receive no artifact.
    op.execute("INSERT INTO limit_admission_requests(signal_id,status,created_at,updated_at) "
               "SELECT id,'pending',created_at,updated_at FROM signals WHERE entry_type='limit'")
    with op.batch_alter_table("initial_publication_daily_slots") as b:
        b.alter_column("publication_id",existing_type=sa.Integer(),nullable=True)
        b.add_column(sa.Column("limit_publication_id",sa.Integer(),nullable=True))
        b.create_foreign_key("fk_initial_publication_daily_slots_limit_publication",
                             "limit_pending_publications",["limit_publication_id"],["id"],ondelete="CASCADE")
        b.create_unique_constraint("uq_initial_publication_daily_slots_limit_publication",["limit_publication_id"])
        b.create_check_constraint("ck_initial_publication_daily_slots_one_owner",
                                  "(publication_id IS NOT NULL) <> (limit_publication_id IS NOT NULL)")
    with op.batch_alter_table("destination_publications") as b:
        b.drop_constraint("ck_destination_publications_initial_no_reply",type_="check")
        b.add_column(sa.Column("origin",sa.Text(),nullable=False,server_default="market"))
        b.create_check_constraint("ck_destination_publications_origin","origin IN ('market','limit_activation')")
        b.create_check_constraint("ck_destination_publications_origin_reply",
            "(origin='market' AND reply_to_message_id IS NULL) OR (origin='limit_activation' AND reply_to_message_id IS NOT NULL)")
    with op.batch_alter_table("signalist_notifications") as b:
        b.add_column(sa.Column("limit_trigger_signal_id",sa.Integer(),nullable=True))
        b.create_foreign_key("fk_signalist_notifications_limit_trigger","limit_entry_triggers",
                             ["limit_trigger_signal_id"],["signal_id"],ondelete="CASCADE")
        b.create_unique_constraint("uq_signalist_notifications_limit_trigger",["limit_trigger_signal_id"])
        b.create_check_constraint("signalist_notification_kind",
            "kind IN ('event','breakeven_prompt','limit_trigger')")
        b.drop_constraint("ck_signalist_notifications_source",type_="check")
        b.create_check_constraint("ck_signalist_notifications_source",
            "(kind='event' AND candidate_id IS NOT NULL AND prompt_id IS NULL AND limit_trigger_signal_id IS NULL) OR "
            "(kind='breakeven_prompt' AND prompt_id IS NOT NULL AND candidate_id IS NULL AND limit_trigger_signal_id IS NULL) OR "
            "(kind='limit_trigger' AND limit_trigger_signal_id IS NOT NULL AND candidate_id IS NULL AND prompt_id IS NULL)")
    op.create_table("limit_cancellation_publications",
        sa.Column("id",sa.Integer(),primary_key=True),sa.Column("signal_id",sa.Integer(),nullable=False),
        sa.Column("destination_id",sa.Integer(),nullable=False),sa.Column("pending_publication_id",sa.Integer(),nullable=False),
        sa.Column("reply_to_message_id",sa.BigInteger(),nullable=False),sa.Column("status",sa.Text(),nullable=False,server_default="pending"),
        sa.Column("telegram_message_id",sa.BigInteger()),sa.Column("attempt_count",sa.Integer(),nullable=False,server_default="0"),
        sa.Column("retry_at",sa.DateTime()),sa.Column("last_error_category",sa.Text()),sa.Column("sent_at",sa.DateTime()),
        sa.Column("created_at",sa.DateTime(),nullable=False),sa.Column("updated_at",sa.DateTime(),nullable=False),
        sa.ForeignKeyConstraint(["signal_id"],["signals.id"],ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["destination_id"],["destinations.id"],ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pending_publication_id"],["limit_pending_publications.id"],ondelete="CASCADE"),
        sa.UniqueConstraint("signal_id","destination_id",name="uq_limit_cancellation_signal_destination"),
        sa.CheckConstraint("reply_to_message_id>0",name="ck_limit_cancellation_reply"),
        sa.CheckConstraint("attempt_count>=0",name="ck_limit_cancellation_attempts"),
        sa.CheckConstraint("(status='sent' AND telegram_message_id IS NOT NULL AND sent_at IS NOT NULL) OR (status<>'sent' AND telegram_message_id IS NULL AND sent_at IS NULL)",name="ck_limit_cancellation_sent"),
        sa.CheckConstraint(f"status IN ({PUBLICATION_STATUSES})",name="limit_cancellation_publication_status"))
    op.create_index("ix_limit_cancellation_status_retry","limit_cancellation_publications",["status","retry_at"])

def downgrade():
    connection=op.get_bind()
    live_limit=connection.execute(sa.text(
        "SELECT COUNT(*) FROM signals WHERE entry_type='limit' OR status IN ('pending_entry','entry_triggered')")).scalar_one()
    if live_limit:
        raise RuntimeError("downgrade blocked: durable Limit Signals must be resolved before removing Phase 5")
    op.drop_index("ix_limit_cancellation_status_retry",table_name="limit_cancellation_publications")
    op.drop_table("limit_cancellation_publications")
    with op.batch_alter_table("signalist_notifications") as b:
        b.drop_constraint("ck_signalist_notifications_source",type_="check")
        b.create_check_constraint("ck_signalist_notifications_source",
            "(kind='event' AND candidate_id IS NOT NULL AND prompt_id IS NULL) OR (kind='breakeven_prompt' AND prompt_id IS NOT NULL AND candidate_id IS NULL)")
        b.drop_constraint("signalist_notification_kind",type_="check")
        b.drop_constraint("uq_signalist_notifications_limit_trigger",type_="unique")
        b.drop_constraint("fk_signalist_notifications_limit_trigger",type_="foreignkey")
        b.drop_column("limit_trigger_signal_id")
    with op.batch_alter_table("destination_publications") as b:
        b.drop_constraint("ck_destination_publications_origin_reply",type_="check")
        b.drop_constraint("ck_destination_publications_origin",type_="check")
        b.drop_column("origin")
        b.create_check_constraint("ck_destination_publications_initial_no_reply","reply_to_message_id IS NULL")
    with op.batch_alter_table("initial_publication_daily_slots") as b:
        b.drop_constraint("ck_initial_publication_daily_slots_one_owner",type_="check")
        b.drop_constraint("uq_initial_publication_daily_slots_limit_publication",type_="unique")
        b.drop_constraint("fk_initial_publication_daily_slots_limit_publication",type_="foreignkey")
        b.drop_column("limit_publication_id")
        b.alter_column("publication_id",existing_type=sa.Integer(),nullable=False)
    op.drop_index("ix_limit_admission_requests_status",table_name="limit_admission_requests")
    op.drop_table("limit_admission_requests")
    with op.batch_alter_table("limit_pending_publications") as b:
        b.drop_constraint("ck_limit_pending_sent",type_="check")
        b.drop_constraint("limit_pending_publication_status",type_="check")
        b.drop_constraint("destination_exchange",type_="check")
        b.drop_column("selected_exchange")
    with op.batch_alter_table("limit_entry_trigger_source_prices") as b:
        b.drop_constraint("destination_exchange",type_="check")
    with op.batch_alter_table("limit_entry_triggers") as b:
        b.drop_constraint("signal_price_source_type",type_="check")
    with op.batch_alter_table("signals") as b:
        b.drop_constraint("signal_entry_type",type_="check")
        b.create_check_constraint("ck_signals_entry_type","entry_type IN ('market','limit')")
    op.create_table("limit_admission_daily_slots",
        sa.Column("id",sa.Integer(),primary_key=True),sa.Column("destination_id",sa.Integer(),nullable=False),
        sa.Column("local_date",sa.Date(),nullable=False),sa.Column("slot_number",sa.Integer(),nullable=False),
        sa.Column("publication_id",sa.Integer(),nullable=False),sa.Column("created_at",sa.DateTime(),nullable=False),
        sa.ForeignKeyConstraint(["destination_id"],["destinations.id"],ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["publication_id"],["limit_pending_publications.id"],ondelete="CASCADE"),
        sa.UniqueConstraint("destination_id","local_date","slot_number",name="uq_limit_slot_destination_date_number"),
        sa.UniqueConstraint("publication_id",name="uq_limit_slot_publication"),
        sa.CheckConstraint("slot_number>0",name="ck_limit_slot_positive"))
