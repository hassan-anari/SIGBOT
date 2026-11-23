"""Opt newly created Limit Signals into automatic activation.

Revision ID: 20261123_01
Revises: 20261026_01
"""
from alembic import op
import sqlalchemy as sa

revision = "20261123_01"
down_revision = "20261026_01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("signals", sa.Column("auto_activate_limit", sa.Boolean(),
                                      nullable=False, server_default=sa.false()))


def downgrade():
    if op.get_bind().execute(sa.text(
        "SELECT 1 FROM signals WHERE auto_activate_limit = 1 LIMIT 1"
    )).scalar_one_or_none():
        raise RuntimeError("downgrade blocked: automatic Limit Signals exist")
    op.drop_column("signals", "auto_activate_limit")
