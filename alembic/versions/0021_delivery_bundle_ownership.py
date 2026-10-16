"""0021: 数据集交付包归属记录

`GET /deliveries/dataset/{bundle_id}/download` 原先只按 bundle_id 从对象存储取件，
不校验调用方是否为该交付所属合约的当事方（UUID 不可猜，但不可猜不等于已授权）。
新增 delivery_bundles 表记录 包 → 合约/会话/创建人 绑定，下载按合约当事方鉴权；
0021 之前生成的包没有归属记录，下载一律 404（诚实拒绝，不按"猜到了就放行"处理）。

Revision ID: 0021_delivery_bundle_ownership
Revises: 0020_webhook_detached_history
Create Date: 2026-10-15
"""
from alembic import op
import sqlalchemy as sa

revision = "0021_delivery_bundle_ownership"
down_revision = "0020_webhook_detached_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "delivery_bundles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("contract_id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("object_name", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("file_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["contract_id"], ["contracts.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["sandbox_sessions.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_delivery_bundles_contract", "delivery_bundles", ["contract_id"], unique=False)
    op.create_index("ix_delivery_bundles_session", "delivery_bundles", ["session_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_delivery_bundles_session", table_name="delivery_bundles")
    op.drop_index("ix_delivery_bundles_contract", table_name="delivery_bundles")
    op.drop_table("delivery_bundles")
