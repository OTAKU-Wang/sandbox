"""OCR 复核修复第二批（poolA2-models）：A203 剩余三列 + A82 操作租约列与排他索引。

正文见 ``docs/ocr-review-2026-10-18/ocr-verified-findings.md`` 的 ``### A203`` /
``### A82``；上一批（A203 的 ``data_resources.file_size_bytes``、A124、A171、
A214）在 revision ``0028_ocr_review_models``，本 revision 只补它明确留给下一批
的部分，不改任何既有 revision。

* **A203** ``trained_models.size_bytes`` / ``delivery_bundles.size_bytes`` /
  ``rag_generative_models.size_bytes``：``INTEGER`` → ``BIGINT``。SQLite 的
  INTEGER 本来就是 64 位，所以只有 PostgreSQL 会坏（>2,147,483,647 字节在
  INSERT 处抛 ``IntegerOutOfRangeError``），本地永远重现不出来；这三列写的正是
  模型 checkpoint / 交付包 / RAG 权重体积，超过 2 GiB 是常规量级。同仓已有正确
  写法 ``cube_templates.size_bytes``（``0014_cube_templates.py``）。
* **A82** ``session_operations`` 补 ``started_at`` / ``finished_at`` 两列，并加
  部分唯一索引 ``uq_session_ops_rollback_inflight``
  ``(session_id, op_type) WHERE op_type = 'rollback' AND status IN
  ('pending','running')``。现状是 ``app/services/session_operations.py`` 用
  SELECT-then-INSERT 做「同一会话只允许一个 rollback」：进程重启后停在
  ``running`` 的僵死行会让该会话永远 ``OPERATION_LOCKED``，而竞态下的两个
  rollback 又都能通过同一道门。索引把「排他」这一侧交给数据库强制，两列给启动
  回收（把租约过期的 ``running`` 判 failed）提供依据。
  predicate 只圈 ``rollback``：``snapshot`` / ``template_build`` 等非排他类型必须
  保持可并发，把 ``status`` 之外的终态也圈进来则会让第二次 rollback 永远写不进库。
  **不加 ``session_id`` 外键**：``cube_template_service.op_session_id()`` 用
  ``uuid5`` 派生一个「并不存在的会话」的 id 来复用本表跑模板构建（该函数注释
  自己写着 "no session exists for builds"），加了外键 PostgreSQL 上第一次
  ``template_build`` 就会 FK 违例。要外键必须先把模板操作挪到独立的键列，那是
  ``app/services/**`` 的改动，见证据文件 NEEDS-OWNER。

方言说明：A203 的 ``ALTER COLUMN ... TYPE`` 只在 PostgreSQL 分支用
``op.alter_column``，其它方言走 ``batch_alter_table`` 重建表；部分索引同时给
``postgresql_where`` 与 ``sqlite_where``，不含 PG-only 语义；本 revision 不含
任何 Boolean 默认值改动。

downgrade 只还原模式，且必须能一路降到 base（``tests/test_migrations.py`` 会跑
``downgrade base`` 并要求库被清空）。

Revision ID: 0029_ocr_size_bigint_op_lease
Revises: 0028_ocr_review_models
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0029_ocr_size_bigint_op_lease"
down_revision = "0028_ocr_review_models"
branch_labels = None
depends_on = None

# A203: (table, column) — the three size columns left over by revision 0028.
_SIZE_COLUMNS = (
    ("trained_models", "size_bytes"),
    ("delivery_bundles", "size_bytes"),
    ("rag_generative_models", "size_bytes"),
)

# A82: only the exclusive op type, only the in-flight statuses.
_OP_LEASE_INDEX = "uq_session_ops_rollback_inflight"
_OP_LEASE_PREDICATE = "op_type = 'rollback' AND status IN ('pending', 'running')"


def _widen_sizes(narrow: bool) -> None:
    """INTEGER <-> BIGINT for the A203 columns (``narrow=True`` reverts)."""
    bind = op.get_bind()
    source = sa.BigInteger() if narrow else sa.Integer()
    target = sa.Integer() if narrow else sa.BigInteger()
    for table, column in _SIZE_COLUMNS:
        if bind.dialect.name == "postgresql":
            op.alter_column(
                table, column,
                existing_type=source, type_=target,
                existing_nullable=False, existing_server_default=sa.text("0"),
            )
        else:
            with op.batch_alter_table(table) as batch:
                batch.alter_column(
                    column,
                    existing_type=source, type_=target,
                    existing_nullable=False, existing_server_default=sa.text("0"),
                )


def upgrade() -> None:
    _widen_sizes(narrow=False)

    op.add_column(
        "session_operations",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "session_operations",
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        _OP_LEASE_INDEX,
        "session_operations",
        ["session_id", "op_type"],
        unique=True,
        postgresql_where=sa.text(_OP_LEASE_PREDICATE),
        sqlite_where=sa.text(_OP_LEASE_PREDICATE),
    )


def downgrade() -> None:
    op.drop_index(_OP_LEASE_INDEX, table_name="session_operations")
    op.drop_column("session_operations", "finished_at")
    op.drop_column("session_operations", "started_at")

    _widen_sizes(narrow=True)
