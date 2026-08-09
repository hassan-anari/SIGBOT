"""Phase 7.1 staged signal lifecycle and durable details text outbox.

Revision ID: 20260809_02
Revises: 20260809_01
"""
from alembic import op
import sqlalchemy as sa

revision="20260809_02"
down_revision="20260809_01"
branch_labels=None
depends_on=None

def upgrade():
    # SQLite batch mode preserves every existing Phase-6 row while replacing the
    # generated enum CHECK and making only stop_loss nullable.
    with op.batch_alter_table("signals", recreate="always") as batch:
        batch.alter_column("stop_loss",existing_type=sa.Text(),nullable=True)
        batch.drop_constraint("signal_lifecycle_status",type_="check")
        batch.create_check_constraint("signal_lifecycle_status",
            "status IN ('pending_details', 'active')")
    op.create_table("signal_details_publications",
        sa.Column("id",sa.Integer(),primary_key=True,autoincrement=True),
        sa.Column("signal_id",sa.Integer(),nullable=False),
        sa.Column("destination_id",sa.Integer(),nullable=False),
        sa.Column("status",sa.String(24),nullable=False),
        sa.Column("telegram_message_id",sa.BigInteger()),
        sa.Column("reply_to_message_id",sa.BigInteger()),
        sa.Column("attempt_count",sa.Integer(),nullable=False,server_default="0"),
        sa.Column("retry_at",sa.DateTime()),sa.Column("first_attempt_at",sa.DateTime()),
        sa.Column("sent_at",sa.DateTime()),sa.Column("last_error_category",sa.Text()),
        sa.Column("created_at",sa.DateTime(),nullable=False),sa.Column("updated_at",sa.DateTime(),nullable=False),
        sa.ForeignKeyConstraint(["signal_id"],["signals.id"],ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["destination_id"],["destinations.id"],ondelete="CASCADE"),
        sa.UniqueConstraint("signal_id","destination_id",name="uq_signal_details_destination"),
        sa.CheckConstraint("status IN ('pending','rendering','sending','retry_wait','sent','failed','delivery_unknown','blocked_template','skipped_inactive','skipped_unassigned','skipped_capacity')",name="destination_publication_status"),
        sa.CheckConstraint("attempt_count >= 0",name="ck_signal_details_attempts"),
        sa.CheckConstraint("telegram_message_id IS NULL OR telegram_message_id > 0",name="ck_signal_details_message"),
        sa.CheckConstraint("reply_to_message_id IS NULL OR reply_to_message_id > 0",name="ck_signal_details_reply"))
    op.create_index("ix_signal_details_status_retry","signal_details_publications",["status","retry_at"])
    op.create_table("initial_publication_notifications",
        sa.Column("id",sa.Integer(),primary_key=True,autoincrement=True),
        sa.Column("signal_id",sa.Integer(),nullable=False),sa.Column("status",sa.Text(),nullable=False),
        sa.Column("outcome",sa.Text(),nullable=False),sa.Column("telegram_message_id",sa.BigInteger()),
        sa.Column("created_at",sa.DateTime(),nullable=False),sa.Column("updated_at",sa.DateTime(),nullable=False),
        sa.Column("sent_at",sa.DateTime()),sa.ForeignKeyConstraint(["signal_id"],["signals.id"],ondelete="CASCADE"),
        sa.UniqueConstraint("signal_id",name="uq_initial_publication_notification_signal"))
    op.create_index("ix_initial_publication_notification_status","initial_publication_notifications",["status","created_at"])

def downgrade():
    # Downgrade is intentionally refused when pending rows exist by SQLite's
    # restored NOT NULL/CHECK constraints; no production data is silently lost.
    op.drop_index("ix_initial_publication_notification_status",table_name="initial_publication_notifications")
    op.drop_table("initial_publication_notifications")
    op.drop_index("ix_signal_details_status_retry",table_name="signal_details_publications")
    op.drop_table("signal_details_publications")
    with op.batch_alter_table("signals",recreate="always") as batch:
        batch.drop_constraint("signal_lifecycle_status",type_="check")
        batch.create_check_constraint("signal_lifecycle_status","status IN ('active')")
        batch.alter_column("stop_loss",existing_type=sa.Text(),nullable=False)
