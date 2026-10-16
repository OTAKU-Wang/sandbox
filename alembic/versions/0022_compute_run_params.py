"""0022: 生产运行参数与规格覆盖

「执行确认」弹窗允许人工触发时指定已发布生产版本、提交运行参数并向下收窄规格。
compute_runs 需要留存本次运行的实际输入（params / resource_override），否则运行记录
无法复现：CDS_PARAMS 注入的是被审代码的一部分，参数本体必须与运行行同存。

Revision ID: 0022_compute_run_params
Revises: 0021_delivery_bundle_ownership
Create Date: 2026-10-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0022_compute_run_params"
down_revision = "0021_delivery_bundle_ownership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("compute_runs", sa.Column("params", sa.JSON(), nullable=True))
    op.add_column("compute_runs", sa.Column("resource_override", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("compute_runs", "resource_override")
    op.drop_column("compute_runs", "params")
