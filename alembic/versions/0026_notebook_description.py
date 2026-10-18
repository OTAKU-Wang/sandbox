"""SS-19 G1: 新增开发任务整页化（图 2-2「基本信息」卡）带来的任务描述列。

notebooks 加列 description（TEXT，可空）。图 2-2 的「任务描述」是非必填项，
没有这一列时前端只能把用户写的正文丢掉；NULL = 未填写，存量行不回填。
"""
import sqlalchemy as sa
from alembic import op

revision = "0026_notebook_description"
down_revision = "0025_notebook_data_resource_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("notebooks", sa.Column("description", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("notebooks", "description")
