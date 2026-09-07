"""W16 — task_queue table (multi-replica durable queue).

Revision ID: 0006_task_queue
Revises: 0005_node_operations
Create Date: 2026-09-07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0006_task_queue"
down_revision = "0005_node_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_queue",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("claimed_by", sa.String(length=128), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("run_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_index("ix_task_queue_task_type", "task_queue", ["task_type"])
    op.create_index("ix_task_queue_status", "task_queue", ["status"])
    op.create_index("ix_task_queue_status_type_created", "task_queue", ["status", "task_type", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_task_queue_status_type_created", table_name="task_queue")
    op.drop_index("ix_task_queue_status", table_name="task_queue")
    op.drop_index("ix_task_queue_task_type", table_name="task_queue")
    op.drop_table("task_queue")
