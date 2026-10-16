"""CB-06 — cube_egress_dedup table (SS-10): idempotency-key dedup for the
cube node egress audit tailer (POST /internal/audit/egress). At-least-once
pushes from the node; unique on (node_id, file_name, offset, line_no).
Additive-only.

Revision ID: 0015_cube_egress_dedup
Revises: 0014_cube_templates
Create Date: 2026-10-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0015_cube_egress_dedup"
down_revision = "0014_cube_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cube_egress_dedup",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("node_id", sa.String(length=128), nullable=False),
        sa.Column("file_name", sa.String(length=512), nullable=False),
        sa.Column("offset", sa.BigInteger(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("node_id", "file_name", "offset", "line_no", name="uq_cube_egress_dedup_key"),
    )
    op.create_index("ix_cube_egress_dedup_node", "cube_egress_dedup", ["node_id"])


def downgrade() -> None:
    op.drop_index("ix_cube_egress_dedup_node", table_name="cube_egress_dedup")
    op.drop_table("cube_egress_dedup")
