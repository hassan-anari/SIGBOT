"""Add durable Signalist notification interaction version.

Revision ID: 20260817_01
Revises: 20260816_01
"""
from alembic import op
import sqlalchemy as sa

revision = "20260817_01"
down_revision = "20260816_01"
branch_labels = None
depends_on = None


def upgrade():
    # All rows predating this migration used the legacy Publish/Decline UI.
    with op.batch_alter_table("signalist_notifications") as batch:
        batch.add_column(sa.Column("interaction_version", sa.Integer(),
                                   nullable=False, server_default="1"))
        batch.create_check_constraint("ck_signalist_notifications_interaction_version",
                                      "interaction_version >= 1")


def downgrade():
    with op.batch_alter_table("signalist_notifications") as batch:
        batch.drop_constraint("ck_signalist_notifications_interaction_version",
                              type_="check")
        batch.drop_column("interaction_version")
