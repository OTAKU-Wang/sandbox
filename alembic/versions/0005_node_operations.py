"""W14 — node operations columns on sandbox_nodes.

Revision ID: 0005_node_operations
Revises: 0004_session_operations
Create Date: 2026-09-07

Additive-only:

- sandbox_nodes.scheduling_disabled  (isolate: cordon the node from selection)
- sandbox_nodes.health_state         (unknown | healthy | stale | isolated)

NOTE (SS-14 e2e D-e2e-08, 2026-10-13): server_default text("0") → text("false").
PostgreSQL rejects an integer default on a boolean column ("column is of type
boolean but default expression is of type integer") — on PG this migration could
never have applied, so no deployed environment carries the old literal and the
in-place fix violates no live schema. SQLite tolerated 0, which is why the
defect survived (tests run on sqlite). ORM parity: app/models/sandbox_node.py.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0005_node_operations"
down_revision = "0004_session_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sandbox_nodes", sa.Column("scheduling_disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("sandbox_nodes", sa.Column("health_state", sa.String(length=16), nullable=False, server_default="unknown"))


def downgrade() -> None:
    op.drop_column("sandbox_nodes", "health_state")
    op.drop_column("sandbox_nodes", "scheduling_disabled")
