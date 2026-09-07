"""W11 — session_operations table for async long-running operations.

Revision ID: 0004_session_operations
Revises: 0003_idle_policy
Create Date: 2026-09-07
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0004_session_operations"
down_revision = "0003_idle_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "session_operations",
        sa.Column("op_id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("op_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("result_ref", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("op_id"),
    )
    op.create_index("ix_session_operations_session_id", "session_operations", ["session_id"], unique=False)
    op.create_index("ix_session_operations_session_created", "session_operations", ["session_id", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_session_operations_session_created", table_name="session_operations")
    op.drop_index("ix_session_operations_session_id", table_name="session_operations")
    op.drop_table("session_operations")
