"""W15 — shared volumes + attachments.

Revision ID: 0007_shared_volumes
Revises: 0006_task_queue
Create Date: 2026-09-07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007_shared_volumes"
down_revision = "0006_task_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shared_volumes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("size_limit_mb", sa.Integer(), nullable=False, server_default="1024"),
        sa.Column("read_only", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("owner_id", "name", name="uq_shared_volume_owner_name"),
    )
    op.create_index("ix_shared_volumes_name", "shared_volumes", ["name"])
    op.create_index("ix_shared_volumes_owner_id", "shared_volumes", ["owner_id"])

    op.create_table(
        "shared_volume_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("volume_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("shared_volumes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("read_only", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("session_id", "volume_id", name="uq_volume_attachment"),
    )
    op.create_index("ix_volume_attachments_session", "shared_volume_attachments", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_volume_attachments_session", table_name="shared_volume_attachments")
    op.drop_table("shared_volume_attachments")
    op.drop_index("ix_shared_volumes_owner_id", table_name="shared_volumes")
    op.drop_index("ix_shared_volumes_name", table_name="shared_volumes")
    op.drop_table("shared_volumes")
