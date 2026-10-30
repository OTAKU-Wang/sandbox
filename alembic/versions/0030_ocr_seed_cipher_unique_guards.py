"""OCR 复核修复第三批（poolA3-models）：A186 key_seed 密文化 + A214 唯一约束单一来源守卫。

正文见 ``docs/ocr-review-2026-10-18/ocr-verified-findings.md`` 的 ``### A186`` /
``### A214``；前两批（A203/A124/A171/A214-mpc、A203 余列/A82/A121/A138）在
revision ``0028_ocr_review_models`` / ``0029_ocr_size_bigint_op_lease``，本
revision 只补 A214 明确派给本轨的两半（E2-8/E2-11）与 A186 的库内落存半边，
不改任何既有 revision。

* **A186** ``watermark_records.key_seed``：明文 ``INTEGER`` → 带标签密文
  ``TEXT``（``cds-ks1:`` + base64(``[nonce|tag|ct]``)，加解密在
  ``app/models/watermark_record.py::EncryptedKeySeed``）。升级顺序固定为
  先放宽列型（int→text，无损），再在应用层逐行封装（AES/SM4 无法在 SQL 内
  完成）；降级对称：先解封装回十进制文本，再收窄回 ``INTEGER``。SQLite 走
  ``batch_alter_table`` 重建（0027/0029 同款），PostgreSQL 直接
  ``op.alter_column``。
* **A214 (E2-8/E2-11)** ``certificates.serial_number`` /
  ``field_visibility_configs.product_id``：0000 基线在「表级 UNIQUE 约束」与
  「同名唯一索引」上各建了一份，模型列又带 ``unique=True``（第三份声明）。
  唯一性的唯一来源收敛为命名唯一索引（与 A124/A171/A214-mpc 同口径），PG 上
  drop 掉基线带入的匿名 UNIQUE 约束；SQLite 的表内 UNIQUE 既不可反射也从不
  被本链引用，无法 drop，也不需要（约束与索引语义一致，索引仍在兜底）。
  **升级前查重、存量重复即中止**（A214 既定策略，同 ``0028`` 的
  ``mpc_key_shares``：重复即数据损坏，本 revision 不裁决、不删行，操作员
  可读报错后人工处置）。检查跑在任何 DDL 之前：SQLite 的 DDL 非事务性，
  中止后的升级必须无副作用、可重跑。

本 revision 不含任何 Boolean 默认值改动。downgrade 只还原模式；A214 半边在
PG 上重建同名匿名 UNIQUE 约束（PG 自动命名规则一致），A186 半边可完整往返
（封装/解封装是双射）。

Revision ID: 0030_ocr_seed_cipher_unique_guards
Revises: 0029_ocr_size_bigint_op_lease
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0030_ocr_seed_cipher_unique_guards"
down_revision = "0029_ocr_size_bigint_op_lease"
branch_labels = None
depends_on = None

# A214: (table, column, index that stays as the single uniqueness source,
# PG name of the duplicate UNIQUE constraint created by 0000 baseline).
_UNIQUE_GUARDS = (
    ("certificates", "serial_number", "ix_certificates_serial",
     "certificates_serial_number_key"),
    ("field_visibility_configs", "product_id", "ix_field_visibility_product",
     "field_visibility_configs_product_id_key"),
)


def _assert_no_duplicates() -> None:
    """A214 fail-loud dedupe policy: existing duplicates ABORT the upgrade.

    Silent resolution is deliberately NOT offered here either: a duplicate
    ``serial_number`` makes certificate lookup/revoke
    (``app/services/certificate_service.py`` resolves by serial with
    ``scalar_one_or_none()``) a permanent 500, and a duplicate
    ``product_id`` means two visibility policies for one product — which one
    applies is a data-recovery decision, not a migration decision.
    """
    bind = op.get_bind()
    offenders = []
    for table, column, index, _constraint in _UNIQUE_GUARDS:
        rows = bind.execute(
            sa.text(
                f"SELECT {column} AS v, COUNT(*) AS c FROM {table} "
                f"GROUP BY {column} HAVING COUNT(*) > 1 ORDER BY c DESC LIMIT 20"
            )
        ).fetchall()
        if rows:
            listing = ", ".join(f"{v!r} x{c}" for v, c in rows)
            offenders.append(f"{table}.{column} (owner index {index}): {listing}")
    if offenders:
        raise RuntimeError(
            "revision 0030 refuses to upgrade: rows already violate the named "
            "unique indexes that this revision keeps as the SINGLE uniqueness "
            "source for A214 (certificates.serial_number / "
            "field_visibility_configs.product_id). No rows are deleted and no "
            "last-writer-wins is applied — merging or removing the duplicate "
            "rows is a data-recovery decision for the operator. Offending "
            f"groups (first 20 per table): {'; '.join(offenders)}"
        )


def _convert_seals(encrypt: bool) -> None:
    """Row-by-row seal/unseal of watermark_records.key_seed (A186).

    Runs while the column is TEXT on both sides, so the same code path works
    on PostgreSQL and SQLite. Re-runnable: already-sealed values are skipped
    on the way up, unsealed values on the way down.
    """
    from app.models.watermark_record import (
        _SEED_LABEL,
        decrypt_key_seed,
        encrypt_key_seed,
    )

    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, key_seed FROM watermark_records")
    ).fetchall()
    update = sa.text("UPDATE watermark_records SET key_seed = :sealed WHERE id = :rid")
    for rid, raw in rows:
        if raw is None:
            raise RuntimeError(
                f"watermark_records row {rid!r} has a NULL key_seed; the column "
                "is NOT NULL — repair the row before upgrading"
            )
        s = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
        sealed = s.startswith(_SEED_LABEL)
        if encrypt:
            if sealed:
                continue
            value = encrypt_key_seed(int(s))
        else:
            if not sealed:
                continue
            value = str(decrypt_key_seed(s))
        bind.execute(update, {"sealed": value, "rid": rid})


def _key_seed_column_type(to_text: bool) -> None:
    bind = op.get_bind()
    source = sa.Integer() if to_text else sa.Text()
    target = sa.Text() if to_text else sa.Integer()
    if bind.dialect.name == "postgresql":
        op.alter_column(
            "watermark_records", "key_seed",
            existing_type=source, type_=target, existing_nullable=False,
        )
    else:
        with op.batch_alter_table("watermark_records") as batch:
            batch.alter_column(
                "key_seed",
                existing_type=source, type_=target, existing_nullable=False,
            )


def upgrade() -> None:
    _assert_no_duplicates()

    if op.get_bind().dialect.name == "postgresql":
        for table, _column, _index, constraint in _UNIQUE_GUARDS:
            op.drop_constraint(constraint, table, type_="unique")

    # A186: widen first (int → text is lossless), then seal row by row.
    _key_seed_column_type(to_text=True)
    _convert_seals(encrypt=True)


def downgrade() -> None:
    _convert_seals(encrypt=False)
    _key_seed_column_type(to_text=False)

    if op.get_bind().dialect.name == "postgresql":
        for table, column, _index, _constraint in _UNIQUE_GUARDS:
            op.create_unique_constraint(None, table, [column])
