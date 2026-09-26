"""Add durable Warm-up cooldown and publication outbox.

Revision ID: 20260813_02
Revises: 20260813_01
"""
from alembic import op
import sqlalchemy as sa

revision = "20260813_02"
down_revision = "20260813_01"
branch_labels = None
depends_on = None

OLD_KINDS = "'initial_photo_caption','signal_details','tp_hit','sl_hit','breakeven','signal_cancelled'"
NEW_KINDS = OLD_KINDS + ",'warmup'"
STATUSES = "'pending','rendering','ready','sending','retry_wait','sent','failed','skipped_inactive','skipped_unassigned','skipped_daily_limit','skipped_signallist_inactive','skipped_destination_inactive','skipped_no_initial_message','skipped_no_event_source','superseded','delivery_unknown','blocked_template'"


def _replace_kind_constraint(kinds):
    # Alembic batch mode recreates the SQLite table while copying every row and
    # recreating its indexes, which is the safe way to change a CHECK constraint.
    with op.batch_alter_table("destination_message_templates", recreate="always") as batch:
        batch.drop_constraint("destination_message_kind", type_="check")
        batch.create_check_constraint("destination_message_kind",
                                      f"message_kind IN ({kinds})")


def upgrade():
    _replace_kind_constraint(NEW_KINDS)
    op.create_table(
        "warmup_requests",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("signalist_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["signalist_id"], ["signalists.id"],
                                name="fk_warmup_requests_signalist"),
    )
    op.create_index("ix_warmup_requests_signalist_created", "warmup_requests",
                    ["signalist_id", "created_at"])
    op.create_table(
        "destination_warmup_cooldowns",
        sa.Column("destination_id", sa.Integer(), primary_key=True),
        sa.Column("last_request_id", sa.Integer(), nullable=False),
        sa.Column("reserved_at", sa.DateTime(), nullable=False),
        sa.Column("next_allowed_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["destination_id"], ["destinations.id"], ondelete="CASCADE",
                                name="fk_warmup_cooldowns_destination"),
        sa.ForeignKeyConstraint(["last_request_id"], ["warmup_requests.id"],
                                name="fk_warmup_cooldowns_request"),
        sa.CheckConstraint("next_allowed_at > reserved_at", name="ck_warmup_cooldowns_window"),
    )
    op.create_index("ix_warmup_cooldowns_next_allowed", "destination_warmup_cooldowns",
                    ["next_allowed_at"])
    op.create_table(
        "warmup_publications",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("destination_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger()),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_at", sa.DateTime()),
        sa.Column("first_attempt_at", sa.DateTime()),
        sa.Column("sent_at", sa.DateTime()),
        sa.Column("last_error_category", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["request_id"], ["warmup_requests.id"], ondelete="CASCADE",
                                name="fk_warmup_publications_request"),
        sa.ForeignKeyConstraint(["destination_id"], ["destinations.id"], ondelete="CASCADE",
                                name="fk_warmup_publications_destination"),
        sa.UniqueConstraint("request_id", "destination_id",
                            name="uq_warmup_publications_request_destination"),
        sa.CheckConstraint(f"status IN ({STATUSES})", name="warmup_publication_status"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_warmup_publications_attempts"),
        sa.CheckConstraint("telegram_message_id IS NULL OR telegram_message_id > 0",
                           name="ck_warmup_publications_message"),
        sa.CheckConstraint("(status = 'sent' AND telegram_message_id IS NOT NULL AND sent_at IS NOT NULL) OR (status <> 'sent' AND telegram_message_id IS NULL AND sent_at IS NULL)",
                           name="ck_warmup_publications_sent"),
    )
    op.create_index("ix_warmup_publications_status_retry", "warmup_publications",
                    ["status", "retry_at"])


def downgrade():
    op.drop_index("ix_warmup_publications_status_retry", table_name="warmup_publications")
    op.drop_table("warmup_publications")
    op.drop_index("ix_warmup_cooldowns_next_allowed", table_name="destination_warmup_cooldowns")
    op.drop_table("destination_warmup_cooldowns")
    op.drop_index("ix_warmup_requests_signalist_created", table_name="warmup_requests")
    op.drop_table("warmup_requests")
    op.execute("DELETE FROM destination_message_templates WHERE message_kind = 'warmup'")
    _replace_kind_constraint(OLD_KINDS)
