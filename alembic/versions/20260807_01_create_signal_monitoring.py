"""Create durable Phase 6A monitoring, events, decisions and notification outbox."""
from alembic import op
import sqlalchemy as sa

revision = "20260807_01"
down_revision = "20260806_01"
branch_labels = None
depends_on = None

DECIMAL = sa.Text()
UTC = sa.Text()


def upgrade() -> None:
    op.create_table("signal_monitor_states",
        sa.Column("signal_id",sa.Integer(),nullable=False),sa.Column("stage",sa.Text(),nullable=False),
        sa.Column("next_tp_index",sa.Integer(),nullable=False,server_default="1"),
        sa.Column("highest_tp_index",sa.Integer(),nullable=False,server_default="0"),
        sa.Column("favorable_threshold_reached_at",UTC),sa.Column("breakeven_armed_at",UTC),
        sa.Column("breakeven_cycle",sa.Integer(),nullable=False,server_default="0"),
        sa.Column("currently_inside_breakeven_zone",sa.Boolean(),nullable=False,server_default=sa.false()),
        sa.Column("breakeven_rearm_reached_at",UTC),sa.Column("last_breakeven_prompt_at",UTC),
        sa.Column("last_checked_at",UTC),sa.Column("last_reference_price",DECIMAL),
        sa.Column("current_result_kind",sa.Text()),sa.Column("current_result_price",DECIMAL),
        sa.Column("current_leveraged_roi",DECIMAL),sa.Column("completed_at",UTC),
        sa.Column("disabled_reason",sa.Text()),sa.Column("version",sa.Integer(),nullable=False,server_default="1"),
        sa.Column("created_at",UTC,nullable=False),sa.Column("updated_at",UTC,nullable=False),
        sa.ForeignKeyConstraint(["signal_id"],["signals.id"],ondelete="CASCADE",name="fk_signal_monitor_states_signal_id_signals"),
        sa.PrimaryKeyConstraint("signal_id",name="pk_signal_monitor_states"),
        sa.CheckConstraint("next_tp_index > 0 AND highest_tp_index >= 0 AND breakeven_cycle >= 0 AND version > 0",name="ck_signal_monitor_states_counters"),
        sa.CheckConstraint("stage IN ('pre_tp1','post_tp1','completed_sl','completed_breakeven','completed_targets','disabled_legacy')",name="ck_signal_monitor_states_stage"),
        sa.CheckConstraint("stage <> 'pre_tp1' OR highest_tp_index = 0",name="ck_signal_monitor_states_pre_tp1"),
        sa.CheckConstraint("stage NOT IN ('completed_sl','completed_breakeven','completed_targets') OR completed_at IS NOT NULL",name="ck_signal_monitor_states_completion"),
        sa.CheckConstraint("stage <> 'completed_breakeven' OR current_leveraged_roi = '0'",name="ck_signal_monitor_states_breakeven_zero"))
    op.create_index("ix_signal_monitor_states_stage_checked","signal_monitor_states",["stage","last_checked_at"])
    op.create_table("signal_events",
        sa.Column("id",sa.Integer(),primary_key=True,autoincrement=True),sa.Column("signal_id",sa.Integer(),nullable=False),
        sa.Column("kind",sa.Text(),nullable=False),sa.Column("tp_index",sa.Integer()),sa.Column("threshold_price",DECIMAL),
        sa.Column("observed_reference_price",DECIMAL,nullable=False),sa.Column("roi_basis_price",DECIMAL,nullable=False),
        sa.Column("leveraged_roi",DECIMAL,nullable=False),sa.Column("price_source_type",sa.Text(),nullable=False),
        sa.Column("occurred_at",UTC,nullable=False),sa.Column("created_at",UTC,nullable=False),
        sa.ForeignKeyConstraint(["signal_id"],["signals.id"],ondelete="CASCADE",name="fk_signal_events_signal_id_signals"),
        sa.UniqueConstraint("signal_id","tp_index",name="uq_signal_events_signal_tp"),
        sa.CheckConstraint("kind IN ('tp_hit','sl_hit','breakeven')",name="ck_signal_events_kind"),
        sa.CheckConstraint("(kind='tp_hit' AND tp_index > 0 AND threshold_price IS NOT NULL) OR (kind<>'tp_hit' AND tp_index IS NULL)",name="ck_signal_events_tp_shape"),
        sa.CheckConstraint("trim(observed_reference_price) <> '' AND CAST(observed_reference_price AS NUMERIC) > 0",name="ck_signal_events_observed_positive"))
    op.create_index("uq_signal_events_one_sl","signal_events",["signal_id"],unique=True,sqlite_where=sa.text("kind='sl_hit'"))
    op.create_index("uq_signal_events_one_breakeven","signal_events",["signal_id"],unique=True,sqlite_where=sa.text("kind='breakeven'"))
    op.create_index("ix_signal_events_signal_occurred","signal_events",["signal_id","occurred_at"])
    op.create_table("signal_event_source_prices",
        sa.Column("event_id",sa.Integer(),nullable=False),sa.Column("exchange",sa.Text(),nullable=False),sa.Column("price",DECIMAL,nullable=False),
        sa.ForeignKeyConstraint(["event_id"],["signal_events.id"],ondelete="CASCADE",name="fk_signal_event_source_prices_event_id_signal_events"),
        sa.PrimaryKeyConstraint("event_id","exchange",name="pk_signal_event_source_prices"),
        sa.CheckConstraint("exchange IN ('ourbit','toobit','lbank')",name="ck_signal_event_source_prices_exchange"),
        sa.CheckConstraint("trim(price) <> '' AND CAST(price AS NUMERIC) > 0",name="ck_signal_event_source_prices_positive"))
    op.create_table("signal_update_candidates",
        sa.Column("id",sa.Integer(),primary_key=True,autoincrement=True),sa.Column("signal_id",sa.Integer(),nullable=False),
        sa.Column("event_id",sa.Integer(),nullable=False),sa.Column("signalist_id",sa.Integer(),nullable=False),
        sa.Column("status",sa.Text(),nullable=False),sa.Column("superseded_by_event_id",sa.Integer()),
        sa.Column("approved_at",UTC),sa.Column("declined_at",UTC),sa.Column("created_at",UTC,nullable=False),sa.Column("updated_at",UTC,nullable=False),
        sa.ForeignKeyConstraint(["signal_id"],["signals.id"],ondelete="CASCADE",name="fk_signal_update_candidates_signal_id_signals"),
        sa.ForeignKeyConstraint(["event_id"],["signal_events.id"],ondelete="CASCADE",name="fk_signal_update_candidates_event_id_signal_events"),
        sa.ForeignKeyConstraint(["signalist_id"],["signalists.id"],name="fk_signal_update_candidates_signalist_id_signalists"),
        sa.ForeignKeyConstraint(["superseded_by_event_id"],["signal_events.id"],name="fk_signal_update_candidates_superseded_event"),
        sa.UniqueConstraint("event_id",name="uq_signal_update_candidates_event_id"),
        sa.CheckConstraint("status IN ('awaiting_notification','awaiting_decision','approved','declined','superseded','ineligible_inactive')",name="ck_signal_update_candidates_status"),
        sa.CheckConstraint("NOT (approved_at IS NOT NULL AND declined_at IS NOT NULL)",name="ck_signal_update_candidates_decision"))
    op.create_index("ix_signal_update_candidates_status_created","signal_update_candidates",["status","created_at"])
    op.create_table("breakeven_prompts",
        sa.Column("id",sa.Integer(),primary_key=True,autoincrement=True),sa.Column("signal_id",sa.Integer(),nullable=False),
        sa.Column("cycle_number",sa.Integer(),nullable=False),sa.Column("status",sa.Text(),nullable=False),
        sa.Column("observed_price_at_creation",DECIMAL,nullable=False),sa.Column("telegram_message_id",sa.BigInteger()),
        sa.Column("created_at",UTC,nullable=False),sa.Column("updated_at",UTC,nullable=False),sa.Column("notified_at",UTC),
        sa.Column("declined_at",UTC),sa.Column("accepted_at",UTC),sa.Column("expired_at",UTC),
        sa.ForeignKeyConstraint(["signal_id"],["signals.id"],ondelete="CASCADE",name="fk_breakeven_prompts_signal_id_signals"),
        sa.UniqueConstraint("signal_id","cycle_number",name="uq_breakeven_prompts_signal_cycle"),
        sa.CheckConstraint("cycle_number > 0",name="ck_breakeven_prompts_cycle_positive"),
        sa.CheckConstraint("status IN ('pending_notification','awaiting_response','declined','accepted','expired','superseded')",name="ck_breakeven_prompts_status"))
    op.create_index("uq_breakeven_prompts_one_open","breakeven_prompts",["signal_id"],unique=True,sqlite_where=sa.text("status IN ('pending_notification','awaiting_response')"))
    op.create_table("signalist_notifications",
        sa.Column("id",sa.Integer(),primary_key=True,autoincrement=True),sa.Column("kind",sa.Text(),nullable=False),
        sa.Column("candidate_id",sa.Integer()),sa.Column("prompt_id",sa.Integer()),sa.Column("signalist_id",sa.Integer(),nullable=False),
        sa.Column("status",sa.Text(),nullable=False),sa.Column("attempt_count",sa.Integer(),nullable=False,server_default="0"),
        sa.Column("retry_at",UTC),sa.Column("telegram_message_id",sa.BigInteger()),sa.Column("last_error_category",sa.Text()),
        sa.Column("created_at",UTC,nullable=False),sa.Column("updated_at",UTC,nullable=False),sa.Column("sent_at",UTC),
        sa.ForeignKeyConstraint(["candidate_id"],["signal_update_candidates.id"],ondelete="CASCADE",name="fk_signalist_notifications_candidate_id_candidates"),
        sa.ForeignKeyConstraint(["prompt_id"],["breakeven_prompts.id"],ondelete="CASCADE",name="fk_signalist_notifications_prompt_id_prompts"),
        sa.ForeignKeyConstraint(["signalist_id"],["signalists.id"],name="fk_signalist_notifications_signalist_id_signalists"),
        sa.UniqueConstraint("candidate_id",name="uq_signalist_notifications_candidate_id"),sa.UniqueConstraint("prompt_id",name="uq_signalist_notifications_prompt_id"),
        sa.CheckConstraint("(kind='event' AND candidate_id IS NOT NULL AND prompt_id IS NULL) OR (kind='breakeven_prompt' AND prompt_id IS NOT NULL AND candidate_id IS NULL)",name="ck_signalist_notifications_source"),
        sa.CheckConstraint("status IN ('pending','sending','retry_wait','sent','failed','delivery_unknown','skipped_inactive','superseded')",name="ck_signalist_notifications_status"),
        sa.CheckConstraint("attempt_count >= 0",name="ck_signalist_notifications_attempts"),
        sa.CheckConstraint("(status='sent' AND telegram_message_id > 0 AND sent_at IS NOT NULL) OR status<>'sent'",name="ck_signalist_notifications_sent"))
    op.create_index("ix_signalist_notifications_status_retry","signalist_notifications",["status","retry_at"])


def downgrade() -> None:
    for table in ("signalist_notifications","breakeven_prompts","signal_update_candidates",
                  "signal_event_source_prices","signal_events","signal_monitor_states"):
        op.drop_table(table)
