"""Support audited manual close events and independent position-action delivery.

Revision ID: 20261026_01
Revises: 20260818_03
"""
from alembic import op
import sqlalchemy as sa

revision = "20261026_01"
down_revision = "20260818_03"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("signal_events") as batch:
        batch.drop_constraint("ck_signal_events_kind", type_="check")
        batch.create_check_constraint("ck_signal_events_kind",
            "kind IN ('tp_hit','sl_hit','breakeven','risk_free_hit','manual_close')")
    op.create_index("uq_signal_events_one_manual_close", "signal_events", ["signal_id"],
                    unique=True, sqlite_where=sa.text("kind='manual_close'"))
    with op.batch_alter_table("signal_position_action_publications") as batch:
        batch.alter_column("candidate_id", existing_type=sa.Integer(), nullable=True)
        batch.alter_column("tp_publication_id", existing_type=sa.Integer(), nullable=True)
        batch.create_check_constraint("ck_position_action_publications_origin",
            "(candidate_id IS NULL AND tp_publication_id IS NULL) OR "
            "(candidate_id IS NOT NULL AND tp_publication_id IS NOT NULL)")


def downgrade():
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT COUNT(*) FROM signal_events WHERE kind='manual_close'")).scalar_one() or connection.execute(sa.text(
            "SELECT COUNT(*) FROM signal_position_action_publications WHERE candidate_id IS NULL OR tp_publication_id IS NULL")).scalar_one():
        raise RuntimeError("downgrade blocked: durable independent Full Close records exist")
    with op.batch_alter_table("signal_position_action_publications") as batch:
        batch.drop_constraint("ck_position_action_publications_origin", type_="check")
        batch.alter_column("candidate_id", existing_type=sa.Integer(), nullable=False)
        batch.alter_column("tp_publication_id", existing_type=sa.Integer(), nullable=False)
    op.drop_index("uq_signal_events_one_manual_close", table_name="signal_events")
    with op.batch_alter_table("signal_events") as batch:
        batch.drop_constraint("ck_signal_events_kind", type_="check")
        batch.create_check_constraint("ck_signal_events_kind",
            "kind IN ('tp_hit','sl_hit','breakeven','risk_free_hit')")
