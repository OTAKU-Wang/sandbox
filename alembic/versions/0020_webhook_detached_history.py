"""0020: 推送投递历史在订阅删除后保留

删除 Webhook 订阅时投递记录仍被引用，旧的外键会抛 ForeignKeyViolation（表现为 500）。
投递记录属交付历史，按保留策略不删，故 subscription_id 改为可空，删除订阅时由 delete_webhook
显式置空子表外键（两边方言行为一致，不依赖 ON DELETE 是否被强制）。

Revision ID: 0020_webhook_detached_history
Revises: 0019_flow_orchestration
Create Date: 2026-10-15
"""
from alembic import op
import sqlalchemy as sa

revision = "0020_webhook_detached_history"
down_revision = "0019_flow_orchestration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column("webhook_deliveries", "subscription_id", existing_type=sa.UUID(), nullable=True)
    else:
        with op.batch_alter_table("webhook_deliveries") as batch:
            batch.alter_column("subscription_id", existing_type=sa.UUID(), nullable=True)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column("webhook_deliveries", "subscription_id", existing_type=sa.UUID(), nullable=False)
    else:
        with op.batch_alter_table("webhook_deliveries") as batch:
            batch.alter_column("subscription_id", existing_type=sa.UUID(), nullable=False)
