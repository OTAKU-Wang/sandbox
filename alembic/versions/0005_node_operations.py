"""W14 — node operations columns on sandbox_nodes.

Revision ID: 0005_node_operations
Revises: 0004_session_operations
Create Date: 2026-09-07

Additive-only:

- sandbox_nodes.scheduling_disabled  (isolate: cordon the node from selection)
- sandbox_nodes.health_state         (unknown | healthy | stale | isolated)
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0005_node_operations"
down_revision = "0004_session_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sandbox_nodes", sa.Column("scheduling_disabled", sa.Boolean(), nullable=False, server_default=sa.text("0")))
    op.add_column("sandbox_nodes", sa.Column("health_state", sa.String(length=16), nullable=False, server_default="unknown"))


def downgrade() -> None:
    op.drop_column("sandbox_nodes", "health_state")
    op.drop_column("sandbox_nodes", "scheduling_disabled")
