"""Persistent deployment-wide model spending, including uncertain billing."""

import sqlalchemy as sa
from alembic import op

revision = "20261019_model_budget"
down_revision = "20261018_feishu_intake"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "model_budget",
        sa.Column("key", sa.String(40), primary_key=True),
        sa.Column("spent_microusd", sa.Integer(), nullable=False),
        sa.Column("reserved_microusd", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "spent_microusd >= 0 AND reserved_microusd >= 0", name="ck_model_budget"
        ),
    )


def downgrade():
    op.drop_table("model_budget")
