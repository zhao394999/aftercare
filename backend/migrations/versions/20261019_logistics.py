"""Keep waybill data and bounded query snapshots without changing old orders."""

import sqlalchemy as sa
from alembic import op

revision = "20261019_logistics"
down_revision = "20261019_model_budget"
branch_labels = None
depends_on = None


def upgrade():
    for name, size in [
        ("carrier_code", 50),
        ("tracking_number", 32),
        ("tracking_phone", 32),
    ]:
        op.add_column(
            "orders",
            sa.Column(name, sa.String(size), nullable=False, server_default=""),
        )
    op.add_column(
        "orders", sa.Column("logistics_queried_at", sa.DateTime(), nullable=True)
    )
    op.add_column(
        "orders",
        sa.Column("logistics_snapshot", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade():
    for name in [
        "logistics_snapshot",
        "logistics_queried_at",
        "tracking_phone",
        "tracking_number",
        "carrier_code",
    ]:
        op.drop_column("orders", name)
