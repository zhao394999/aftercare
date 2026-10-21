"""Enforce financial ledger invariants and index durable dispatch."""

from alembic import op

revision = "20261018_ledger"
down_revision = "20261018_tenant_rls"
branch_labels = None
depends_on = None
CHECK = "paid_cents > 0 AND refunded_cents >= 0 AND reserved_cents >= 0 AND refunded_cents + reserved_cents <= paid_cents"


def upgrade():
    if op.get_bind().dialect.name == "sqlite":
        # Preserve existing referenced orders; SQLite cannot ADD CHECK without rebuilding.
        invalid = "NOT (NEW.paid_cents > 0 AND NEW.refunded_cents >= 0 AND NEW.reserved_cents >= 0 AND NEW.refunded_cents + NEW.reserved_cents <= NEW.paid_cents)"
        for event in ["INSERT", "UPDATE"]:
            op.execute(
                f"CREATE TRIGGER ledger_{event.lower()} BEFORE {event} ON orders WHEN {invalid} BEGIN SELECT RAISE(ABORT,'order ledger invariant'); END"
            )
    else:
        op.create_check_constraint("ck_order_ledger", "orders", CHECK)
    op.create_index(
        "ix_jobs_dispatch", "jobs", ["state", "available_at", "lease_until"]
    )


def downgrade():
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER ledger_insert")
        op.execute("DROP TRIGGER ledger_update")
    else:
        op.drop_constraint("ck_order_ledger", "orders", type_="check")
    op.drop_index("ix_jobs_dispatch", table_name="jobs")
