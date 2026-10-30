"""A6: connectors.api_key_prefix 加宽 varchar(8) -> varchar(16)

注册/轮换接口写入的是密钥随机段的显示前缀（宽度取自模型列，见
``app.models.connector.derive_key_prefix``）。旧列宽 8 装不下任何能标识密钥的
前缀（``cds-conn-`` 方案头就占 9 字符），SQLite 静默放行、真 PostgreSQL 必抛
``value too long for type character varying(8)``，注册接口必 500。
宽度对齐 SS-12 ``api_keys.key_prefix`` 的 String(16)。存量行为方案头截断的
旧前缀，加宽后可容纳，不回填、不伪造。

Revision ID: 0027_connector_key_prefix_width
Revises: 0026_notebook_description
Create Date: 2026-10-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0027_connector_key_prefix_width"
down_revision = "0026_notebook_description"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column(
            "connectors", "api_key_prefix",
            existing_type=sa.String(length=8), type_=sa.String(length=16),
            existing_nullable=False,
        )
    else:
        with op.batch_alter_table("connectors") as batch:
            batch.alter_column(
                "api_key_prefix",
                existing_type=sa.String(length=8), type_=sa.String(length=16),
                existing_nullable=False,
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column(
            "connectors", "api_key_prefix",
            existing_type=sa.String(length=16), type_=sa.String(length=8),
            existing_nullable=False,
        )
    else:
        with op.batch_alter_table("connectors") as batch:
            batch.alter_column(
                "api_key_prefix",
                existing_type=sa.String(length=16), type_=sa.String(length=8),
                existing_nullable=False,
            )
