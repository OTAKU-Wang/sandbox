"""A170（backend/low 收敛）：删掉 webhook 两表的同前缀冗余单列索引。

`0019` 为 `webhook_subscriptions` / `webhook_deliveries` 各物化了形状相同的
一对索引：单列（列上的 ``index=True`` 自动名）与以该列为首列的复合索引。
复合索引的最左前缀已完整服务单列查询与 FK 支撑，单列那一支只剩写放大与
EXPLAIN 噪声——0028 对 `mpc_key_shares.ix_mpc_shares_key` 是同一口径的收口
（见其 A214 段），本 revision 把剩下的两处照同一形状收掉：

- ``ix_webhook_subscriptions_user_id``（保留 ``ix_webhook_subs_user_status``
  ``(user_id, status)``）；
- ``ix_webhook_deliveries_subscription_id``（保留
  ``ix_webhook_deliveries_sub_status`` ``(subscription_id, status)``）。

与模型成对：`app/models/webhook.py` / `app/models/webhook_delivery.py` 同步
去掉这两列的 ``index=True``，否则 create_all 与 alembic 头在
`scripts/verify_schema_parity.py` 下重新发散。`watermark_records` 的同型重复
已由 0028 收口，不在此列。

downgrade 忠实重建 0019 的两条单列索引。

Revision ID: 0033_ocr_webhook_index_convergence
Revises: 0032_refresh_sessions
Create Date: 2026-10-30
"""
from alembic import op

revision = "0033_ocr_webhook_index_convergence"
down_revision = "0032_refresh_sessions"
branch_labels = None
depends_on = None

_SUBS_SINGLE = "ix_webhook_subscriptions_user_id"
_DELIV_SINGLE = "ix_webhook_deliveries_subscription_id"


def upgrade() -> None:
    op.drop_index(_SUBS_SINGLE, table_name="webhook_subscriptions")
    op.drop_index(_DELIV_SINGLE, table_name="webhook_deliveries")


def downgrade() -> None:
    op.create_index(_SUBS_SINGLE, "webhook_subscriptions", ["user_id"], unique=False)
    op.create_index(_DELIV_SINGLE, "webhook_deliveries", ["subscription_id"], unique=False)
