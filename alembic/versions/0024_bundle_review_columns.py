"""SS-18 P0-②: 交付中心展示列落库（T5 审查状态 / 内容水印状态 / 数据表行数）。

此前这三项只写在包内 MANIFEST.json，交付中心列表要显示就得整包下载或干脆不显示
（list_dataset_bundles 的旧注释就是这个理由）。这里把打包当时就已算出的结论写进行：
只加列、不回填，存量包保持 NULL 并由前端显示「未记录」，绝不反推成已审/已加水印。
"""
import sqlalchemy as sa
from alembic import op

revision = "0024_bundle_review_columns"
down_revision = "0023_task_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("delivery_bundles", sa.Column("t5_review_status", sa.String(24), nullable=True))
    op.add_column("delivery_bundles", sa.Column("watermark_status", sa.String(24), nullable=True))
    op.add_column("delivery_bundles", sa.Column("row_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("delivery_bundles", "row_count")
    op.drop_column("delivery_bundles", "watermark_status")
    op.drop_column("delivery_bundles", "t5_review_status")
