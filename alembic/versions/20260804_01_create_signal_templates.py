"""Create production signal template metadata.

Revision ID: 20260804_01
Revises: 20260801_01
"""
from alembic import op
import sqlalchemy as sa
revision="20260804_01";down_revision="20260801_01";branch_labels=None;depends_on=None
UTC_DEFAULT=sa.text("(strftime('%Y-%m-%dT%H:%M:%f000+00:00', 'now'))")
def upgrade():
 op.create_table("signal_templates",
  sa.Column("id",sa.Integer(),autoincrement=True,nullable=False),sa.Column("exchange",sa.String(6),nullable=False),
  sa.Column("destination_id",sa.Integer(),nullable=True),sa.Column("storage_key",sa.Text(),nullable=False),sa.Column("sha256",sa.Text(),nullable=False),
  sa.Column("width",sa.Integer(),nullable=False),sa.Column("height",sa.Integer(),nullable=False),sa.Column("content_type",sa.Text(),nullable=False),
  sa.Column("created_at",sa.Text(),server_default=UTC_DEFAULT,nullable=False),sa.Column("updated_at",sa.Text(),server_default=UTC_DEFAULT,nullable=False),
  sa.PrimaryKeyConstraint("id",name="pk_signal_templates"),sa.ForeignKeyConstraint(["destination_id"],["destinations.id"],ondelete="CASCADE",name="fk_signal_templates_destination_id_destinations"),
  sa.CheckConstraint("exchange IN ('ourbit','toobit','lbank')",name="signal_template_exchange"),
  sa.CheckConstraint("trim(storage_key) <> '' AND substr(storage_key,1,1) <> '/' AND instr(storage_key,'..') = 0",name="ck_signal_templates_storage_key_safe"),
  sa.CheckConstraint("width > 0 AND height > 0",name="ck_signal_templates_dimensions_positive"),
  sa.CheckConstraint("(exchange='ourbit' AND width=1080 AND height=1148) OR (exchange='toobit' AND width=942 AND height=1010) OR (exchange='lbank' AND width=830 AND height=1005)",name="ck_signal_templates_exchange_dimensions"),
  sa.CheckConstraint("content_type = 'image/png'",name="ck_signal_templates_content_type_png"))
 op.create_index("uq_signal_templates_default_exchange","signal_templates",["exchange"],unique=True,sqlite_where=sa.text("destination_id IS NULL"))
 op.create_index("uq_signal_templates_destination_exchange","signal_templates",["destination_id","exchange"],unique=True,sqlite_where=sa.text("destination_id IS NOT NULL"))
def downgrade():op.drop_table("signal_templates")
