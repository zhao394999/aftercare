"""Keep team-table intake idempotent without changing existing tickets."""

import sqlalchemy as sa
from alembic import op

revision = "20261018_feishu_intake"
down_revision = "20261018_worker_health"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tickets", sa.Column("source_ref", sa.String(330), nullable=True))
    op.create_index(
        "uq_ticket_source_ref", "tickets", ["tenant_id", "source_ref"], unique=True
    )


def downgrade():
    op.drop_index("uq_ticket_source_ref", table_name="tickets")
    op.drop_column("tickets", "source_ref")
