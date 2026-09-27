"""Add durable position state and weighted accounting ledger.

Revision ID: 20260815_01
Revises: 20260814_01
"""
from alembic import op
import sqlalchemy as sa

revision = "20260815_01"
down_revision = "20260814_01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("signal_position_states",
        sa.Column("signal_id",sa.Integer(),nullable=False),
        sa.Column("remaining_fraction",sa.Text(),nullable=False),
        sa.Column("realized_weighted_roi",sa.Text(),nullable=False),
        sa.Column("risk_free_used",sa.Boolean(),nullable=False),
        sa.Column("risk_free_active",sa.Boolean(),nullable=False),
        sa.Column("save_profit_50_used",sa.Boolean(),nullable=False),
        sa.Column("effective_stop_price",sa.Text(),nullable=True),
        sa.Column("is_closed",sa.Boolean(),nullable=False),
        sa.Column("closed_at",sa.Text(),nullable=True),
        sa.Column("version",sa.Integer(),nullable=False),
        sa.Column("created_at",sa.Text(),nullable=False),
        sa.Column("updated_at",sa.Text(),nullable=False),
        sa.ForeignKeyConstraint(["signal_id"],["signals.id"],ondelete="CASCADE",name="fk_signal_position_states_signal"),
        sa.CheckConstraint("CAST(remaining_fraction AS NUMERIC) >= 0 AND CAST(remaining_fraction AS NUMERIC) <= 1",name="ck_signal_position_states_fraction"),
        sa.CheckConstraint("version >= 1",name="ck_signal_position_states_version"),
        sa.CheckConstraint("(is_closed = 1 AND CAST(remaining_fraction AS NUMERIC) = 0 AND closed_at IS NOT NULL) OR (is_closed = 0 AND CAST(remaining_fraction AS NUMERIC) > 0 AND closed_at IS NULL)",name="ck_signal_position_states_closed_shape"),
        sa.CheckConstraint("risk_free_active = 0 OR (risk_free_used = 1 AND effective_stop_price IS NOT NULL AND is_closed = 0)",name="ck_signal_position_states_risk_free"),
        sa.PrimaryKeyConstraint("signal_id",name="pk_signal_position_states"))
    op.create_index("ix_signal_position_states_open","signal_position_states",["is_closed","signal_id"])
    op.create_table("signal_position_actions",
        sa.Column("id",sa.Integer(),autoincrement=True,nullable=False),
        sa.Column("signal_id",sa.Integer(),nullable=False),
        sa.Column("event_id",sa.Integer(),nullable=False),
        sa.Column("candidate_id",sa.Integer(),nullable=True),
        sa.Column("action_kind",sa.Enum("normal_tp","risk_free","save_profit_50","full_close","final_tp_close","stop_loss_close","risk_free_hit_close",name="position_action_kind",native_enum=False,create_constraint=True),nullable=False),
        sa.Column("remaining_fraction_before",sa.Text(),nullable=False),
        sa.Column("closed_fraction",sa.Text(),nullable=False),
        sa.Column("remaining_fraction_after",sa.Text(),nullable=False),
        sa.Column("execution_price",sa.Text(),nullable=False),
        sa.Column("leveraged_roi",sa.Text(),nullable=False),
        sa.Column("weighted_roi_delta",sa.Text(),nullable=False),
        sa.Column("realized_weighted_roi_after",sa.Text(),nullable=False),
        sa.Column("created_at",sa.Text(),nullable=False),
        sa.ForeignKeyConstraint(["signal_id"],["signals.id"],ondelete="CASCADE",name="fk_signal_position_actions_signal"),
        sa.ForeignKeyConstraint(["event_id"],["signal_events.id"],name="fk_signal_position_actions_event"),
        sa.ForeignKeyConstraint(["candidate_id"],["signal_update_candidates.id"],name="fk_signal_position_actions_candidate"),
        sa.UniqueConstraint("candidate_id",name="uq_signal_position_actions_candidate"),
        sa.UniqueConstraint("event_id","action_kind",name="uq_signal_position_actions_event_kind"),
        sa.CheckConstraint("CAST(remaining_fraction_before AS NUMERIC) >= 0 AND CAST(remaining_fraction_before AS NUMERIC) <= 1 AND CAST(closed_fraction AS NUMERIC) >= 0 AND CAST(remaining_fraction_after AS NUMERIC) >= 0 AND CAST(remaining_fraction_after AS NUMERIC) <= CAST(remaining_fraction_before AS NUMERIC) AND CAST(remaining_fraction_before AS NUMERIC) - CAST(closed_fraction AS NUMERIC) = CAST(remaining_fraction_after AS NUMERIC)",name="ck_signal_position_actions_fractions"),
        sa.CheckConstraint("(CAST(closed_fraction AS NUMERIC) = 0 AND CAST(weighted_roi_delta AS NUMERIC) = 0) OR CAST(closed_fraction AS NUMERIC) > 0",name="ck_signal_position_actions_zero_close"),
        sa.PrimaryKeyConstraint("id",name="pk_signal_position_actions"))
    op.create_index("ix_signal_position_actions_signal_created","signal_position_actions",["signal_id","created_at"])


def downgrade():
    op.drop_index("ix_signal_position_actions_signal_created",table_name="signal_position_actions")
    op.drop_table("signal_position_actions")
    op.drop_index("ix_signal_position_states_open",table_name="signal_position_states")
    op.drop_table("signal_position_states")
