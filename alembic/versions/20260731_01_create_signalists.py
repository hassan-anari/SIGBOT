"""Create the Signalist persistence foundation.

Revision ID: 20260731_01
Revises: None
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260731_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

status_enum = postgresql.ENUM(
    "active", "inactive", "archived", name="signalist_status", create_type=False
)


def upgrade() -> None:
    status_enum.create(op.get_bind(), checkfirst=False)
    op.create_table(
        "signalists",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("telegram_username", sa.Text(), nullable=True),
        sa.Column("status", status_enum, server_default=sa.text("'active'::signalist_status"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_signalists"),
        sa.UniqueConstraint("telegram_user_id", name="uq_signalists_telegram_user_id"),
        sa.CheckConstraint("telegram_user_id > 0", name="ck_signalists_telegram_user_id_positive"),
        sa.CheckConstraint("btrim(display_name) <> ''", name="ck_signalists_display_name_nonempty"),
        sa.CheckConstraint(
            "(status = 'archived' AND archived_at IS NOT NULL) OR "
            "(status <> 'archived' AND archived_at IS NULL)",
            name="ck_signalists_archive_consistency",
        ),
    )
    op.create_index("ix_signalists_status", "signalists", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_signalists_status", table_name="signalists")
    op.drop_table("signalists")
    status_enum.drop(op.get_bind(), checkfirst=False)
