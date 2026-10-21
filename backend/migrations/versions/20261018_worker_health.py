"""Track background worker liveness independently of API readiness."""

from alembic import op
import sqlalchemy as sa

revision = "20261018_worker_health"
down_revision = "20261018_ledger"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("jobs", sa.Column("dispatched_at", sa.DateTime(), nullable=True))
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(100), primary_key=True),
        sa.Column("seen_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("worker_heartbeats")
    op.drop_column("jobs", "dispatched_at")
