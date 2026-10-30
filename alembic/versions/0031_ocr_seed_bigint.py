"""OCR 复核修复（backend-seed-a）：A186 最后一条腿 —— ``key_seed`` 熵从 31 位加宽到 63 位。

正文见 ``docs/ocr-review-2026-10-18/ocr-verified-findings.md`` 的 ``### A186``：
「水印的全部安全性 = 这颗 31-bit 种子」。密文落存那一半已在 revision
``0030_ocr_seed_cipher_unique_guards`` 落地（并明确把「种子熵加宽」留给下一轮），
本 revision 只补这一条，不改任何既有 revision。

**加宽的是逻辑域，不是 ``ALTER … TYPE bigint``。** 0030 之后
``watermark_records.key_seed`` 的物理列型已经是无界 ``TEXT``（``cds-ks1:`` +
base64(``[nonce|tag|ct]``) 封套）。在这种形状上执行 ``ALTER COLUMN key_seed TYPE
bigint`` 会把密文强转成整数 —— PostgreSQL 直接 ``cannot cast type text to
bigint``，SQLite 的 batch 重建则把封套静默清成 0，两者都不可接受。真正的加宽在应用侧：
``app/models/watermark_record.py::EncryptedKeySeed`` 的整型基类
``Integer``→``BigInteger``，``app/api/training.py::_key_seed_column_max()`` 因此
报 0…2**63-1，``tests/test_ocr_a167_key_seed_bounds_before_save.py`` 同轮改钉。

本 revision 负责另一半：**保证落存一定装得下 63 位种子的封套**（最宽封套 =
``cds-ks1:`` 8 字符 + base64(12+16+19)=64 字符 = 72 字符）。做法是反射实际列型，
按形状分流，而不是照抄一条假定形状的 ALTER：

* 无界 ``TEXT``（0030 正常升级得到的形状）→ 校验后不动 DDL（真实读数：0029→0030→0031
  一路升级后 ``sqlite_master`` 里仍是 ``key_seed TEXT NOT NULL``）；
* 整型（遗留库：``create_all`` + ``alembic stamp`` 的 dev/test 库按本仓约定不跑
  0030 的封装，或任何仍停在 ``INTEGER`` 的库）→ 先无损放宽为 ``TEXT``，再逐行封装
  （口径与 0030 完全一致，可重跑：已封装的行跳过）；
* 带长度上限且装不下 72 字符的字符列 → 放宽为无界 ``TEXT`` 并补封装；
* 其它列型 → 中止（不猜列型）。

downgrade 是**有意的 no-op**：无界 ``TEXT`` 对 0030 时代的代码同样可读（``EncryptedKeySeed``
的 ``impl`` 一直是 ``Text``），而把它收窄回 ``INTEGER`` 会摧毁密文 —— 收窄属于
0030 的 downgrade（先解封装再收窄），且 ``2**62`` 一类取值收窄必然溢出。

本 revision 不含任何 Boolean 默认值改动，不新增列、不新增索引。

Revision ID: 0031_ocr_seed_bigint
Revises: 0030_ocr_seed_cipher_unique_guards
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0031_ocr_seed_bigint"
down_revision = "0030_ocr_seed_cipher_unique_guards"
branch_labels = None
depends_on = None

TABLE = "watermark_records"
COLUMN = "key_seed"
# Widest sealed envelope the widened domain can produce: "cds-ks1:" (8) +
# base64(nonce 12 + tag 16 + ciphertext = ASCII digits of 2**63-1 = 19) = 8 + 64.
_MIN_SEAL_CHARS = 72


def _column_type():
    """The column type as the target database actually holds it (never assumed)."""
    inspector = sa.inspect(op.get_bind())
    for col in inspector.get_columns(TABLE):
        if col["name"] == COLUMN:
            return col["type"]
    raise RuntimeError(
        f"{TABLE}.{COLUMN} is missing — cannot verify the key_seed storage width"
    )


def _classify(col_type):
    if isinstance(col_type, sa.Text) and getattr(col_type, "length", None) is None:
        return "text"  # the shape revision 0030 produces — already wide enough
    if isinstance(col_type, sa.Integer):
        return "integer"  # legacy/stamped shape: 31-bit int, would clip the domain
    if isinstance(col_type, sa.String):
        length = getattr(col_type, "length", None)
        if length is not None and length < _MIN_SEAL_CHARS:
            return "bounded"
        return "fits"
    raise RuntimeError(
        f"{TABLE}.{COLUMN} has column type {col_type!r}, which this revision cannot "
        "reason about — refusing to guess whether a 63-bit seed fits"
    )


def _widen_to_text(col_type) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.alter_column(
            TABLE, COLUMN,
            existing_type=col_type, type_=sa.Text(), existing_nullable=False,
        )
    else:
        with op.batch_alter_table(TABLE) as batch:
            batch.alter_column(
                COLUMN,
                existing_type=col_type, type_=sa.Text(), existing_nullable=False,
            )


def _seal_rows() -> None:
    """Row-by-row seal of any still-plaintext seed (same contract as revision 0030).

    Re-runnable: already-sealed values are skipped. A value that is neither a
    blob nor an integer raises — never a silently wrong seed.
    """
    from app.models.watermark_record import (
        _SEED_LABEL,
        decrypt_key_seed,
        encrypt_key_seed,
    )

    bind = op.get_bind()
    rows = bind.execute(sa.text(f"SELECT id, {COLUMN} FROM {TABLE}")).fetchall()
    update = sa.text(f"UPDATE {TABLE} SET {COLUMN} = :sealed WHERE id = :rid")
    for rid, raw in rows:
        if raw is None:
            raise RuntimeError(
                f"{TABLE} row {rid!r} has a NULL {COLUMN}; the column is NOT NULL — "
                "repair the row before upgrading"
            )
        s = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
        sealed = s.startswith(_SEED_LABEL)
        if sealed:
            decrypt_key_seed(s)  # must still open, or the blob is corrupt
            continue
        bind.execute(update, {"sealed": encrypt_key_seed(int(s)), "rid": rid})


def upgrade() -> None:
    kind = _classify(_column_type())
    if kind in ("text", "fits"):
        # Verified no-op: the physical column is width-free (or long enough for
        # the widest envelope), so the widened domain needs no DDL.
        return
    _widen_to_text(_column_type())
    _seal_rows()


def downgrade() -> None:
    # Deliberately a no-op (see the docstring): unbounded TEXT is readable by the
    # pre-A186b code as well, and narrowing would destroy the sealed blobs.
    return
