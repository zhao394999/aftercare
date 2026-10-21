"""Allow read-only knowledge questions to use the budgeted agent runtime."""

import sqlalchemy as sa
from alembic import op

revision = "20261020_knowledge"
down_revision = "20261019_logistics"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "sqlite":
        # SQLite cannot relax NOT NULL without rebuilding a referenced table.
        with op.get_context().autocommit_block():
            op.execute("PRAGMA foreign_keys=OFF")
            try:
                with op.batch_alter_table("agent_runs") as batch:
                    batch.alter_column(
                        "ticket_id", existing_type=sa.String(36), nullable=True
                    )
            finally:
                op.execute("PRAGMA foreign_keys=ON")
    else:
        op.alter_column(
            "agent_runs", "ticket_id", existing_type=sa.String(36), nullable=True
        )


def downgrade():
    raise RuntimeError("Knowledge runs must be archived before restoring NOT NULL")
