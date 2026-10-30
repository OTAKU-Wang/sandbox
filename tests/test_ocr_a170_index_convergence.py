"""A170 — webhook 两表的同前缀冗余单列索引必须保持收口状态。

0019 曾为 `webhook_subscriptions.user_id` / `webhook_deliveries.subscription_id`
各物化一对索引（单列自动名 + 以其为首列的复合索引），纯写放大。0033 把旧库那
一支 drop 掉、两模型成对摘除 `index=True`；本文件钉住收口的两面：

1. 模型侧：`create_all` 路径的元数据里不得再长出那两条单列索引（复合索引必须在）
   —— 否则 verify_schema_parity 与 0033 立即发散；
2. 链侧：0033 挂在 0032 之后、单链，upgrade 真 drop 而 downgrade 忠实重建。

往返用子进程跑（test_migrations 同款模式）：`alembic/env.py` 在模块级把
`get_settings().DATABASE_URL` 无条件写回 sqlalchemy.url，进程内改 Config 会被
覆盖，只有子进程 + 独立 `CDS_DATABASE_URL` 才能真正打在 tmp 库上。
"""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from alembic.script import ScriptDirectory

import app.models.webhook as webhook_model
import app.models.webhook_delivery as delivery_model

REPO = Path(__file__).resolve().parents[1]
REVISION = "0033_ocr_webhook_index_convergence"
SUBS_SINGLE = "ix_webhook_subscriptions_user_id"
DELIV_SINGLE = "ix_webhook_deliveries_subscription_id"


def _index_names(model) -> set[str]:
    return {idx.name for idx in model.__table__.indexes}


def _alembic(target: str, db: Path) -> None:
    env = {k: v for k, v in os.environ.items()
           if k.lower() not in ("http_proxy", "https_proxy", "http_proxy", "https_proxy")}
    env.update(NO_PROXY="*", CDS_DATABASE_URL=f"sqlite+aiosqlite:///{db}")
    subprocess.run(
        [sys.executable, "-m", "alembic", *target.split()],
        check=True, cwd=REPO, env=env, capture_output=True, timeout=300,
    )


def _webhook_indexes(db: Path) -> set[str]:
    with sqlite3.connect(db) as conn:
        return {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'ix_webhook%'")}


def test_create_all_metadata_has_no_redundant_single_indexes():
    subs = _index_names(webhook_model.WebhookSubscription)
    assert SUBS_SINGLE not in subs, (
        "user_id 的自动单列索引回来 = 又和 ix_webhook_subs_user_status 最左前缀重复"
        "（A170 写放大复发），且与 0033 之后的 alembic 头发散"
    )
    assert "ix_webhook_subs_user_status" in subs
    assert "ix_webhook_subs_contract" in subs

    deliv = _index_names(delivery_model.WebhookDelivery)
    assert DELIV_SINGLE not in deliv, "同型：subscription_id 单列支与 sub_status 复合支重复"
    assert "ix_webhook_deliveries_sub_status" in deliv
    assert "ix_webhook_deliveries_status_retry" in deliv


def test_chain_has_exactly_one_head_and_0033_stays_on_the_linear_trunk():
    """a186b 同款祖先形：「头恰等于 0033」是历史快照当契约，下一个合法 revision
    落地就会红。正确的钉子：**全链恒单头**（分叉才多头）且 **0033 永远在 head
    的祖先历史里**（改写/抹除已发布历史才红）；再加 0033 自身挂点
    down_revision=0032——已发布 revision 的父级不可变，红了即历史被改写。
    """
    from alembic.config import Config

    script = ScriptDirectory.from_config(Config(str(REPO / "alembic.ini")))
    heads = script.get_heads()
    assert len(heads) == 1, f"全链必须恒单头（分叉才多头），实际 {heads}"
    ancestors = {rev.revision for rev in script.walk_revisions(head=heads[0])}
    assert REVISION in ancestors, (
        f"{REVISION} 不在 head {heads[0]} 的祖先历史里：已发布历史被改写/抹除"
    )
    assert script.get_revision(REVISION).down_revision == "0032_refresh_sessions"


def test_upgrade_drops_and_downgrade_restores(tmp_path):
    """真跑：升级后单列索引消失、复合索引健在；回滚后忠实重建 0019 的两条。"""
    db = tmp_path / "a170_roundtrip.db"
    _alembic(f"upgrade {REVISION}", db)
    assert _webhook_indexes(db) == {
        "ix_webhook_subs_user_status", "ix_webhook_subs_contract",
        "ix_webhook_deliveries_sub_status", "ix_webhook_deliveries_status_retry",
    }, "0033 之后只应剩复合最左前缀两支 + contract/status_retry，单列冗余支必须已 drop"
    _alembic("downgrade -1", db)
    assert {SUBS_SINGLE, DELIV_SINGLE} <= _webhook_indexes(db), \
        "downgrade 必须忠实重建 0019 的两条单列索引"
