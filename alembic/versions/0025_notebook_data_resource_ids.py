"""SS-19 G2: 开发任务可只注入合约授权集里的部分数据资源（图 2-2「已选择数据」面板）。

notebooks 加列 data_resource_ids（JSON 数组，元素是数据资源 id 字符串）。
NULL = 不限定，注入合约授权的全部数据资源 —— 存量行为不变，存量行不回填
（additive-only 纪律：只加列，不把老任务悄悄改成"选中了 0 个"）。
"""
import sqlalchemy as sa
from alembic import op

revision = "0025_notebook_data_resource_ids"
down_revision = "0024_bundle_review_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("notebooks", sa.Column("data_resource_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("notebooks", "data_resource_ids")
