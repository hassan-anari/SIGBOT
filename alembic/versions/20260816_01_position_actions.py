"""Add Phase 4B position actions and secondary publication outbox.

Revision ID: 20260816_01
Revises: 20260815_01
"""
from alembic import op
import sqlalchemy as sa
revision="20260816_01";down_revision="20260815_01";branch_labels=None;depends_on=None
PUB="pending','rendering','ready','sending','retry_wait','sent','failed','skipped_inactive','skipped_unassigned','skipped_daily_limit','skipped_signallist_inactive','skipped_destination_inactive','skipped_no_initial_message','skipped_no_event_source','superseded','delivery_unknown','blocked_template"
OLD_KINDS="initial_photo_caption','signal_details','tp_hit','sl_hit','breakeven','signal_cancelled','warmup"
NEW_KINDS=OLD_KINDS+"','risk_free_activated','save_profit_50','full_close','risk_free_hit"
OLD_STAGES="pre_tp1','post_tp1','completed_sl','completed_breakeven','completed_targets','disabled_legacy"
NEW_STAGES=OLD_STAGES+"','awaiting_final_tp_action','completed_full_close','completed_risk_free"

def upgrade():
 with op.batch_alter_table('signal_monitor_states') as b:
  b.drop_constraint('ck_signal_monitor_states_stage',type_='check')
  b.drop_constraint('ck_signal_monitor_states_completion',type_='check')
  b.create_check_constraint('ck_signal_monitor_states_stage',f"stage IN ('{NEW_STAGES}')")
  b.create_check_constraint('ck_signal_monitor_states_completion',"stage NOT IN ('completed_sl','completed_breakeven','completed_targets','completed_full_close','completed_risk_free') OR completed_at IS NOT NULL")
 with op.batch_alter_table('signal_events') as b:
  b.drop_constraint('ck_signal_events_kind',type_='check')
  b.create_check_constraint('ck_signal_events_kind',"kind IN ('tp_hit','sl_hit','breakeven','risk_free_hit')")
 op.create_index('uq_signal_events_one_risk_free_hit','signal_events',['signal_id'],unique=True,sqlite_where=sa.text("kind='risk_free_hit'"))
 with op.batch_alter_table('destination_message_templates') as b:
  b.alter_column('message_kind',existing_type=sa.Enum('initial_photo_caption','signal_details','tp_hit','sl_hit','breakeven','signal_cancelled','warmup',name='destination_message_kind',native_enum=False),type_=sa.Enum('initial_photo_caption','signal_details','tp_hit','sl_hit','breakeven','signal_cancelled','warmup','risk_free_activated','save_profit_50','full_close','risk_free_hit',name='destination_message_kind',native_enum=False),existing_nullable=False)
 op.create_table('signal_position_action_publications',
  sa.Column('id',sa.Integer(),primary_key=True),sa.Column('action_id',sa.Integer(),nullable=False),sa.Column('candidate_id',sa.Integer(),nullable=False),sa.Column('signal_id',sa.Integer(),nullable=False),sa.Column('destination_id',sa.Integer(),nullable=False),sa.Column('tp_publication_id',sa.Integer(),nullable=False),sa.Column('status',sa.Text(),nullable=False),sa.Column('reply_to_message_id',sa.BigInteger()),sa.Column('telegram_message_id',sa.BigInteger()),sa.Column('attempt_count',sa.Integer(),nullable=False,server_default='0'),sa.Column('retry_at',sa.Text()),sa.Column('first_attempt_at',sa.Text()),sa.Column('sent_at',sa.Text()),sa.Column('last_error_category',sa.Text()),sa.Column('created_at',sa.Text(),nullable=False),sa.Column('updated_at',sa.Text(),nullable=False),
  sa.ForeignKeyConstraint(['action_id'],['signal_position_actions.id'],ondelete='CASCADE',name='fk_position_action_publications_action'),sa.ForeignKeyConstraint(['candidate_id'],['signal_update_candidates.id'],ondelete='CASCADE',name='fk_position_action_publications_candidate'),sa.ForeignKeyConstraint(['signal_id'],['signals.id'],ondelete='CASCADE',name='fk_position_action_publications_signal'),sa.ForeignKeyConstraint(['destination_id'],['destinations.id'],ondelete='CASCADE',name='fk_position_action_publications_destination'),sa.ForeignKeyConstraint(['tp_publication_id'],['destination_update_publications.id'],ondelete='CASCADE',name='fk_position_action_publications_tp_publication'),sa.UniqueConstraint('action_id','destination_id',name='uq_position_action_publications_action_destination'),sa.CheckConstraint(f"status IN ('{PUB}')",name='position_action_publication_status'),sa.CheckConstraint('attempt_count >= 0',name='ck_position_action_publications_attempts'),sa.CheckConstraint('telegram_message_id IS NULL OR telegram_message_id > 0',name='ck_position_action_publications_message'),sa.CheckConstraint('reply_to_message_id IS NULL OR reply_to_message_id > 0',name='ck_position_action_publications_reply'),sa.CheckConstraint("(status = 'sent' AND telegram_message_id IS NOT NULL AND reply_to_message_id IS NOT NULL AND sent_at IS NOT NULL) OR (status <> 'sent' AND telegram_message_id IS NULL AND sent_at IS NULL)",name='ck_position_action_publications_sent'))
 op.create_index('ix_position_action_publications_status_retry','signal_position_action_publications',['status','retry_at']);op.create_index('ix_position_action_publications_chain','signal_position_action_publications',['signal_id','destination_id','sent_at'])

def downgrade():
 op.drop_table('signal_position_action_publications')
 op.execute("DELETE FROM destination_message_templates WHERE message_kind IN ('risk_free_activated','save_profit_50','full_close','risk_free_hit')")
 with op.batch_alter_table('destination_message_templates') as b:
  b.alter_column('message_kind',existing_type=sa.Enum('initial_photo_caption','signal_details','tp_hit','sl_hit','breakeven','signal_cancelled','warmup','risk_free_activated','save_profit_50','full_close','risk_free_hit',name='destination_message_kind',native_enum=False),type_=sa.Enum('initial_photo_caption','signal_details','tp_hit','sl_hit','breakeven','signal_cancelled','warmup',name='destination_message_kind',native_enum=False),existing_nullable=False)
 op.execute("DELETE FROM signal_events WHERE kind='risk_free_hit'")
 op.drop_index('uq_signal_events_one_risk_free_hit',table_name='signal_events')
 with op.batch_alter_table('signal_events') as b:
  b.drop_constraint('ck_signal_events_kind',type_='check');b.create_check_constraint('ck_signal_events_kind',"kind IN ('tp_hit','sl_hit','breakeven')")
 op.execute("UPDATE signal_monitor_states SET stage='completed_targets' WHERE stage='completed_full_close'")
 op.execute("UPDATE signal_monitor_states SET stage='completed_breakeven' WHERE stage='completed_risk_free'")
 op.execute("UPDATE signal_monitor_states SET stage='post_tp1', completed_at=NULL WHERE stage='awaiting_final_tp_action'")
 with op.batch_alter_table('signal_monitor_states') as b:
  b.drop_constraint('ck_signal_monitor_states_stage',type_='check');b.drop_constraint('ck_signal_monitor_states_completion',type_='check')
  b.create_check_constraint('ck_signal_monitor_states_stage',f"stage IN ('{OLD_STAGES}')")
  b.create_check_constraint('ck_signal_monitor_states_completion',"stage NOT IN ('completed_sl','completed_breakeven','completed_targets') OR completed_at IS NOT NULL")
