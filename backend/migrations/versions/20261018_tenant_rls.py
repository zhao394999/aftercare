"""Force tenant RLS and protect append-only audit records on PostgreSQL."""

from alembic import op

revision = "20261018_tenant_rls"
down_revision = "81ecdf5042a7"
branch_labels = None
depends_on = None

TABLES = [
    "orders",
    "import_jobs",
    "tickets",
    "evidence",
    "policies",
    "proposals",
    "action_executions",
    "communications",
    "audit_events",
    "agent_runs",
    "run_events",
    "model_calls",
]


def upgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'''CREATE POLICY tenant_isolation ON "{table}"
                       USING (tenant_id = nullif(current_setting('app.tenant_id',true),''))
                       WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id',true),''))''')
    op.execute("""CREATE FUNCTION prevent_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
                  BEGIN RAISE EXCEPTION 'audit records are append-only'; END; $$""")
    op.execute("""CREATE TRIGGER audit_append_only BEFORE UPDATE OR DELETE ON audit_events
                  FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation()""")


def downgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP TRIGGER audit_append_only ON audit_events")
    op.execute("DROP FUNCTION prevent_audit_mutation()")
    for table in TABLES:
        op.execute(f'DROP POLICY tenant_isolation ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
