"""SS-18 P0-①: 开发任务字段落库（任务 = 开发笔记本，§2 决策，不另起任务表）。

notebooks 加列：task_no（TASK-YYYYMMDD-NNN，服务端按当日序号生成，唯一索引兜底并发）、
debug_method（调试方法单选）、export_mode（任务级成果导出方式 bundle/openapi）、
debug_memory_mb / exec_memory_mb（调试与执行内存，MB；执行默认继承调试，上限受合约
sandbox_resource_spec.memory_mb 封顶 —— 与 compute 规格覆盖同口径，只向下收窄）。

只加列、不回填：存量笔记本 task_no 为 NULL，前端按「未编号」渲染（additive-only 纪律）。
"""
import sqlalchemy as sa
from alembic import op

revision = "0023_task_fields"
down_revision = "0022_compute_run_params"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("notebooks", sa.Column("task_no", sa.String(32), nullable=True))
    op.add_column("notebooks", sa.Column("debug_method", sa.String(16), nullable=True))
    op.add_column("notebooks", sa.Column("export_mode", sa.String(16), nullable=True))
    op.add_column("notebooks", sa.Column("debug_memory_mb", sa.Integer(), nullable=True))
    op.add_column("notebooks", sa.Column("exec_memory_mb", sa.Integer(), nullable=True))
    op.create_index("ix_notebooks_task_no", "notebooks", ["task_no"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_notebooks_task_no", table_name="notebooks")
    op.drop_column("notebooks", "exec_memory_mb")
    op.drop_column("notebooks", "debug_memory_mb")
    op.drop_column("notebooks", "export_mode")
    op.drop_column("notebooks", "debug_method")
    op.drop_column("notebooks", "task_no")
