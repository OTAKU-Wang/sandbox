"""P2-5（D-e2e-18，ss-19 §10.1-5）：MinIO 一致性巡检的对账标注列。

`data_resources.status=ready` 与桶内对象存在性之间历史上没有对账通道：
早期 fixture 注入时 `S3Error … NoSuchKey`（票据诚实记 `injected: []`），
ready 行照常留在库里，运营侧没有任何一面能看见「这行数据已经取不回来了」。

本 revision 给 `data_resources` 追加两列（与 `app/models/data_resource.py`
成对，verify_schema_parity 门）：

- ``integrity_status`` ∈ {ok, missing, uncheckable}，**默认 'uncheckable'**：
  存量行与新建行在未被巡检 head 确认之前一律「未核实」——诚实铁律，禁止
  用默认值冒认 ok（MinIO 不可达时巡检同样只写 uncheckable）。
- ``integrity_checked_at``：最近一次巡检时刻，NULL = 从未巡检。

不加索引：巡检查询走 `status`（已有 ix_data_resources_provider_status
最左前缀覆盖 provider 维度）+ 表体量小，加索引只添写放大。

downgrade 忠实删列。

Revision ID: 0034_integrity_patrol_columns
Revises: 0033_ocr_webhook_index_convergence
Create Date: 2026-10-30
"""
from alembic import op
import sqlalchemy as sa

revision = "0034_integrity_patrol_columns"
down_revision = "0033_ocr_webhook_index_convergence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "data_resources",
        sa.Column("integrity_status", sa.String(16), nullable=False,
                  server_default=sa.text("'uncheckable'")),
    )
    op.add_column(
        "data_resources",
        sa.Column("integrity_checked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("data_resources", "integrity_checked_at")
    op.drop_column("data_resources", "integrity_status")
