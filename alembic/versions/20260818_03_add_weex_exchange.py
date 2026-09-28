"""Add WEEX exchange persistence support.

Revision ID: 20260818_03
Revises: 20260818_02
"""
from alembic import op
import sqlalchemy as sa

revision="20260818_03"
down_revision="20260818_02"
branch_labels=None
depends_on=None

EX3="'ourbit','toobit','lbank'"
EX4=EX3+",'weex'"
PS3="'composite','ourbit_only','toobit_only','lbank_only'"
PS4=PS3+",'weex_only'"
S6="'ourbit_prices','ourbit_catalog','toobit_prices','toobit_catalog','lbank_prices','lbank_catalog'"
S8=S6+",'weex_prices','weex_catalog'"

EXCHANGE_CONSTRAINTS=(
 ("destination_exchange_priorities","destination_exchange","exchange"),
 ("signals","destination_exchange","selected_exchange",True),
 ("signal_exchange_symbols","signal_exchange","exchange"),
 ("signal_initial_prices","signal_initial_price_exchange","exchange"),
 ("signal_templates","signal_template_exchange","exchange"),
 ("destination_publications","ck_destination_publications_exchange","selected_exchange"),
 ("signal_event_source_prices","ck_signal_event_source_prices_exchange","exchange"),
 ("destination_update_publications","ck_destination_update_publications_exchange","selected_exchange"),
 ("outage_incidents","ck_outage_incidents_exchange","exchange"),
 ("service_health_states","ck_service_health_exchange","exchange"),
 ("signal_comment_publications","destination_exchange","selected_exchange",True),
 ("limit_entry_trigger_source_prices","destination_exchange","exchange"),
 ("limit_pending_publications","destination_exchange","selected_exchange"),
)

def _exchange_constraints(values):
    for item in EXCHANGE_CONSTRAINTS:
        table,name,column,*nullable=item
        expression=f"{column} IN ({values})"
        if nullable: expression=f"{column} IS NULL OR {expression}"
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(name,type_="check")
            batch.create_check_constraint(name,expression)

def _replace(table,name,expression):
    with op.batch_alter_table(table) as batch:
        batch.drop_constraint(name,type_="check")
        batch.create_check_constraint(name,expression)

def upgrade():
    _exchange_constraints(EX4)
    _replace("signals","signal_price_source_type",f"price_source_type IN ({PS4})")
    _replace("limit_entry_triggers","signal_price_source_type",f"price_source_type IN ({PS4})")
    _replace("signal_templates","ck_signal_templates_exchange_dimensions",
             "(exchange='ourbit' AND width=1080 AND height=1148) OR (exchange='toobit' AND width=942 AND height=1010) OR (exchange='lbank' AND width=830 AND height=1005) OR (exchange='weex' AND width=1000 AND height=1000)")
    _replace("service_health_states","ck_service_health_key",f"service_key IN ({S8})")
    _replace("outage_incidents","ck_outage_incidents_key",f"service_key IN ({S8})")

def downgrade():
    connection=op.get_bind()
    checks=[(table,column) for table,_,column,*_ in EXCHANGE_CONSTRAINTS]
    checks += [("signals","price_source_type"),("limit_entry_triggers","price_source_type")]
    for table,column in checks:
        if connection.execute(sa.text(f"SELECT COUNT(*) FROM {table} WHERE {column}='weex' OR {column}='weex_only'")).scalar_one():
            raise RuntimeError("downgrade blocked: durable WEEX rows exist")
    _replace("outage_incidents","ck_outage_incidents_key",f"service_key IN ({S6})")
    _replace("service_health_states","ck_service_health_key",f"service_key IN ({S6})")
    _replace("signal_templates","ck_signal_templates_exchange_dimensions",
             "(exchange='ourbit' AND width=1080 AND height=1148) OR (exchange='toobit' AND width=942 AND height=1010) OR (exchange='lbank' AND width=830 AND height=1005)")
    _replace("limit_entry_triggers","signal_price_source_type",f"price_source_type IN ({PS3})")
    _replace("signals","signal_price_source_type",f"price_source_type IN ({PS3})")
    _exchange_constraints(EX3)
