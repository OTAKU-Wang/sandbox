"""OCR 复核修复（poolA-models）：模式层四件事，一条 revision 装完。

每条改动标注它属于哪条 finding（正文见
``docs/ocr-review-2026-10-18/ocr-verified-findings.md``）：

* **A203** ``data_resources.file_size_bytes`` ``INTEGER`` → ``BIGINT``。
  SQLite 的 INTEGER 本来就是 64 位，因此 **只有 PostgreSQL 会坏**：上传
  >2,147,483,647 字节（约 2.1 GB）的文件在 INSERT 处抛
  ``IntegerOutOfRangeError``，本地永远重现不出来。同仓已有正确写法
  （``cube_template.size_bytes``），这是漏改而不是口径未知。
  本 revision 只改本仓轨道所属的那一列；finding 里另外三列
  （``trained_models.size_bytes`` / ``delivery_bundles.size_bytes`` /
  ``rag_generative_models.size_bytes``）必须与各自模型一起改，见证据文件
  NEEDS-OWNER。
* **A124** ``federation_trusts`` 加 partial UNIQUE ``(local_space_id,
  remote_space_id) WHERE status = 'active'``。装载器
  ``load_trusts_from_db()`` 无 ORDER BY 逐行覆盖 ``space_id -> trust``，
  同一空间对多一条 active 就等于把「重启后生效的信任等级」交给行返回顺序。
  先按 ``(created_at, id)`` 保留最新一条再建索引；predicate 只覆盖
  ``active``：``establish_trust()`` 永远 INSERT 新行，把 ``suspended``
  也圈进来会让「暂停→重新建立信任」再也写不进库。
* **A171** ``watermark_records.job_id`` 由普通索引改为 UNIQUE。两处读
  （``app/api/training.py`` 的嵌入前置检查与 verify）都用
  ``scalar_one_or_none()``，多一行就永久 500。先按 ``(created_at, id)``
  保留最早一条（输掉竞态的那条）再 DROP/CREATE 同名索引为 UNIQUE——
  用同名而不是再加一条唯一索引，避免自己造出 A214 抱怨的重复索引族。
* **A214 (E2-37)** ``mpc_key_shares`` 加 UNIQUE ``(key_id, share_index)``
  并删掉被它前缀覆盖的 ``ix_mpc_shares_key``。同一 x 坐标两行意味着两个
  不同的 y：重构要么除零，要么（因为 ``share_reconstruct`` 只看手里这几
  份）重建出一个「看起来能解密、其实是错的」密钥。存量重复**不自动删**：
  那是各存管方的密钥材料，静默选一份等于伪造一个可恢复的假象，因此这里
  选择 fail loud，由运维核对（重新分发该密钥或人工清理）后再升级。

顺序上有讲究：可能中止的检查（A214）放在任何 DDL 之前。SQLite 的 DDL 不
进事务（alembic 会打印 "Will assume non-transactional DDL"），先做检查可以
保证中止时库面一点没动、重试不会因为半个 revision 落地而失败。

方言说明（rule 3）：A203 的 ``ALTER COLUMN ... TYPE`` 只在 PostgreSQL 分支
用 ``op.alter_column``，其它方言走 ``batch_alter_table`` 重建表；两条 partial
唯一索引都同时给了 ``postgresql_where`` 与 ``sqlite_where``，没有 PG-only 的
隐藏语义；本 revision 不含任何 Boolean 默认值改动。

downgrade 只还原模式：被清理的竞态重复行（watermark / federation）无法复原。

Revision ID: 0028_ocr_review_models
Revises: 0027_connector_key_prefix_width
Create Date: 2026-10-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0028_ocr_review_models"
down_revision = "0027_connector_key_prefix_width"
branch_labels = None
depends_on = None

_WATERMARK_JOB_INDEX = "ix_watermark_records_job_id"
_TRUST_PAIR_INDEX = "uq_federation_trusts_active_pair"
_SHARE_PAIR_INDEX = "uq_mpc_key_shares_key_index"
_SHARE_KEY_INDEX = "ix_mpc_shares_key"

# A171: keep the earliest row per job_id; the later one is the race loser.
_DEDUPE_WATERMARKS = """
DELETE FROM watermark_records
 WHERE id IN (
       SELECT w.id FROM watermark_records w
        WHERE EXISTS (
              SELECT 1 FROM watermark_records o
               WHERE o.job_id = w.job_id
                 AND (o.created_at < w.created_at
                      OR (o.created_at = w.created_at AND o.id < w.id))))
"""

# A124: keep the newest active row per space pair — the most recent
# establishment is the agreement the loader is supposed to reload.
_DEDUPE_TRUSTS = """
DELETE FROM federation_trusts
 WHERE id IN (
       SELECT n.id FROM federation_trusts n
        WHERE EXISTS (
              SELECT 1 FROM federation_trusts a
               WHERE a.local_space_id = n.local_space_id
                 AND a.remote_space_id = n.remote_space_id
                 AND a.status = 'active'
                 AND n.status = 'active'
                 AND (a.created_at > n.created_at
                      OR (a.created_at = n.created_at AND a.id > n.id))))
"""

_DUPLICATE_SHARES = """
SELECT key_id, share_index, COUNT(*) AS n
  FROM mpc_key_shares
 GROUP BY key_id, share_index
HAVING COUNT(*) > 1
 ORDER BY key_id, share_index
"""


def upgrade() -> None:
    bind = op.get_bind()

    # ── A214 first: this is the only step that may abort the revision ──
    duplicates = bind.execute(sa.text(_DUPLICATE_SHARES)).fetchall()
    if duplicates:
        shown = ", ".join(f"{row[0]}!/{row[1]}×{row[2]}" for row in duplicates[:10])
        raise RuntimeError(
            "A214 refuses to upgrade: mpc_key_shares holds several rows for the same "
            f"(key_id, share_index) — {len(duplicates)} coordinate(s), e.g. {shown}. "
            "Two Shamir shares on one x-coordinate mean reconstruction either divides "
            "by zero or rebuilds the wrong key, and silently picking one custodian's "
            "value would fabricate a key that looks recoverable. Re-issue the affected "
            "key(s) (or reconcile the rows by hand), then run `alembic upgrade head` again."
        )

    # ── A203: 4-byte size column is only 4 bytes on PostgreSQL ──
    if bind.dialect.name == "postgresql":
        op.alter_column(
            "data_resources", "file_size_bytes",
            existing_type=sa.Integer(), type_=sa.BigInteger(),
            existing_nullable=True,
        )
    else:
        with op.batch_alter_table("data_resources") as batch:
            batch.alter_column(
                "file_size_bytes",
                existing_type=sa.Integer(), type_=sa.BigInteger(),
                existing_nullable=True,
            )

    # ── A171: dedupe, then rebuild the same index as UNIQUE ──
    op.execute(sa.text(_DEDUPE_WATERMARKS))
    op.drop_index(_WATERMARK_JOB_INDEX, table_name="watermark_records")
    op.create_index(_WATERMARK_JOB_INDEX, "watermark_records", ["job_id"], unique=True)

    # ── A124: one active trust row per space pair ──
    op.execute(sa.text(_DEDUPE_TRUSTS))
    op.create_index(
        _TRUST_PAIR_INDEX,
        "federation_trusts",
        ["local_space_id", "remote_space_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )

    # ── A214: the pair is unique; the plain leading-column index goes away ──
    op.drop_index(_SHARE_KEY_INDEX, table_name="mpc_key_shares")
    op.create_index(_SHARE_PAIR_INDEX, "mpc_key_shares", ["key_id", "share_index"], unique=True)


def downgrade() -> None:
    op.drop_index(_SHARE_PAIR_INDEX, table_name="mpc_key_shares")
    op.create_index(_SHARE_KEY_INDEX, "mpc_key_shares", ["key_id"])

    op.drop_index(_TRUST_PAIR_INDEX, table_name="federation_trusts")

    op.drop_index(_WATERMARK_JOB_INDEX, table_name="watermark_records")
    op.create_index(_WATERMARK_JOB_INDEX, "watermark_records", ["job_id"], unique=False)

    if op.get_bind().dialect.name == "postgresql":
        op.alter_column(
            "data_resources", "file_size_bytes",
            existing_type=sa.BigInteger(), type_=sa.Integer(),
            existing_nullable=True,
        )
    else:
        with op.batch_alter_table("data_resources") as batch:
            batch.alter_column(
                "file_size_bytes",
                existing_type=sa.BigInteger(), type_=sa.Integer(),
                existing_nullable=True,
            )
