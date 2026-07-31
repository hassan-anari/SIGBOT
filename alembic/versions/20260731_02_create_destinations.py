"""Create Phase 4 destination configuration.

Revision ID: 20260731_02
Revises: 20260731_01
"""
from alembic import op
import sqlalchemy as sa

revision = "20260731_02"
down_revision = "20260731_01"
branch_labels = None
depends_on = None

UTC_DEFAULT = sa.text("(strftime('%Y-%m-%dT%H:%M:%f000+00:00', 'now'))")

def upgrade() -> None:
    op.create_table("destinations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("internal_name", sa.Text(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("destination_type", sa.String(7), nullable=False),
        sa.Column("access_level", sa.String(7), nullable=False),
        sa.Column("status", sa.String(8), server_default="inactive", nullable=False),
        sa.Column("daily_limit", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Text(), server_default=UTC_DEFAULT, nullable=False),
        sa.Column("updated_at", sa.Text(), server_default=UTC_DEFAULT, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_destinations"),
        sa.UniqueConstraint("internal_name", name="uq_destinations_internal_name"),
        sa.UniqueConstraint("telegram_chat_id", name="uq_destinations_telegram_chat_id"),
        sa.CheckConstraint("trim(internal_name) <> ''", name="ck_destinations_internal_name_nonempty"),
        sa.CheckConstraint("telegram_chat_id <> 0", name="ck_destinations_telegram_chat_id_nonzero"),
        sa.CheckConstraint("daily_limit > 0", name="ck_destinations_daily_limit_positive"),
        sa.CheckConstraint("destination_type IN ('channel', 'group')", name="destination_type"),
        sa.CheckConstraint("access_level IN ('public', 'private')", name="destination_access_level"),
        sa.CheckConstraint("status IN ('active', 'inactive')", name="destination_status"))
    op.create_index("ix_destinations_status_id", "destinations", ["status", "id"])
    op.create_table("destination_exchange_priorities",
        sa.Column("destination_id", sa.Integer(), nullable=False),
        sa.Column("exchange", sa.String(6), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("destination_id", "exchange", name="pk_destination_exchange_priorities"),
        sa.ForeignKeyConstraint(["destination_id"], ["destinations.id"], ondelete="CASCADE",
                                name="fk_destination_exchange_priorities_destination_id_destinations"),
        sa.UniqueConstraint("destination_id", "position", name="uq_destination_exchange_priorities_destination_position"),
        sa.CheckConstraint("position >= 1", name="ck_destination_exchange_priorities_position_positive"),
        sa.CheckConstraint("exchange IN ('ourbit', 'toobit', 'lbank')", name="destination_exchange"))
    op.create_index("ix_destination_exchange_priorities_destination_position",
                    "destination_exchange_priorities", ["destination_id", "position"])
    op.create_table("destination_signalists",
        sa.Column("destination_id", sa.Integer(), nullable=False),
        sa.Column("signalist_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Text(), server_default=UTC_DEFAULT, nullable=False),
        sa.PrimaryKeyConstraint("destination_id", "signalist_id", name="pk_destination_signalists"),
        sa.ForeignKeyConstraint(["destination_id"], ["destinations.id"], ondelete="CASCADE",
                                name="fk_destination_signalists_destination_id_destinations"),
        sa.ForeignKeyConstraint(["signalist_id"], ["signalists.id"],
                                name="fk_destination_signalists_signalist_id_signalists"))
    op.create_index("ix_destination_signalists_signalist_id_destination_id",
                    "destination_signalists", ["signalist_id", "destination_id"])

def downgrade() -> None:
    op.drop_index("ix_destination_signalists_signalist_id_destination_id", table_name="destination_signalists")
    op.drop_table("destination_signalists")
    op.drop_index("ix_destination_exchange_priorities_destination_position", table_name="destination_exchange_priorities")
    op.drop_table("destination_exchange_priorities")
    op.drop_index("ix_destinations_status_id", table_name="destinations")
    op.drop_table("destinations")
