"""Phase 7.3 durable destination message templates.

Revision ID: 20260809_03
Revises: 20260809_02
"""
from alembic import op
import sqlalchemy as sa

revision = "20260809_03"
down_revision = "20260809_02"
branch_labels = None
depends_on = None

KINDS = "'initial_photo_caption','signal_details','tp_hit','sl_hit','breakeven','signal_cancelled'"

def upgrade():
    op.create_table(
        "destination_message_templates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("message_kind", sa.String(32), nullable=False),
        sa.Column("destination_id", sa.Integer(), nullable=True),
        sa.Column("template_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["destination_id"], ["destinations.id"], ondelete="CASCADE",
                                name="fk_destination_message_templates_destination_id_destinations"),
        sa.CheckConstraint(f"message_kind IN ({KINDS})", name="destination_message_kind"),
        sa.CheckConstraint("trim(template_text) <> ''", name="ck_destination_message_templates_text_nonempty"),
    )
    op.create_index("uq_destination_message_templates_global_kind", "destination_message_templates", ["message_kind"],
                    unique=True, sqlite_where=sa.text("destination_id IS NULL"))
    op.create_index("uq_destination_message_templates_destination_kind", "destination_message_templates",
                    ["destination_id", "message_kind"], unique=True,
                    sqlite_where=sa.text("destination_id IS NOT NULL"))
    op.create_index("ix_destination_message_templates_destination", "destination_message_templates", ["destination_id"])

def downgrade():
    op.drop_index("ix_destination_message_templates_destination", table_name="destination_message_templates")
    op.drop_index("uq_destination_message_templates_destination_kind", table_name="destination_message_templates")
    op.drop_index("uq_destination_message_templates_global_kind", table_name="destination_message_templates")
    op.drop_table("destination_message_templates")
