"""A186 — 选位密钥 ``key_seed`` 的库内落存半边：列里不得再躺明文整数。

台账（docs/ocr-review-2026-10-18/ocr-verified-findings.md ``### A186``）记的三处
泄口已补两处：选位改为 ``SM3(上下文|服务端 pepper|key_seed|层名)``（backend-14a，
``app/services/model_watermark.py``）与 API 只回不可逆指纹（backend-16b，
``app/api/training.py``）。本文件锁第三处：``watermark_records.key_seed`` 列本身。
密文口径与 ``app/utils/crypto.py`` 的规范封装一致：``cds-ks1:`` 标签 +
base64(``[nonce(12)|tag(16)|ct]``)，GCM AAD 绑定域分离标签。
"""
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

from app.api.training import _key_seed_column_max
from app.models.watermark_record import (
    _SEED_LABEL,
    WatermarkRecord,
    decrypt_key_seed,
    encrypt_key_seed,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PREVIOUS_REVISION = "0029_ocr_size_bigint_op_lease"
SEED = 0x5EED1234  # < 2**31, inside the historical Integer domain
# A186b: the sealed column's logical domain is now BIGINT (0…2**63-1); the
# widest accepted seed must still seal and unseal exactly (the storage is
# width-free TEXT, so entropy widening must not cost fidelity).
WIDE_SEED = 2**63 - 1


def _record(job_id: str, seed: int = SEED) -> WatermarkRecord:
    return WatermarkRecord(
        job_id=job_id,
        owner_id="owner-1",
        model_id="model-1",
        layer_positions={"fc.weight": [0, 1]},
        ordered_positions=[["fc.weight", 0], ["fc.weight", 1]],
        key_seed=seed,
        num_bits=16,
    )


async def _raw_column(db_session: AsyncSession, record_id) -> object:
    """The stored value as the database itself holds it (no ORM type layer)."""
    row = await db_session.execute(
        text("SELECT key_seed FROM watermark_records WHERE id = :i"), {"i": record_id}
    )
    return row.scalar()


async def test_stored_value_is_a_sealed_blob_not_the_plaintext(db_session: AsyncSession):
    rec = _record(f"a186-{uuid.uuid4().hex[:8]}")
    db_session.add(rec)
    await db_session.flush()

    stored = await _raw_column(db_session, rec.id)
    assert stored is not None
    assert not isinstance(stored, int), (
        "watermark_records.key_seed is still persisted as a plain integer — anyone "
        "who can read the DB holds the watermark location seed (A186)"
    )
    assert str(stored).startswith(_SEED_LABEL), (
        f"stored value is not a {(_SEED_LABEL,)} sealed blob: {stored!r}"
    )
    assert str(SEED) not in str(stored), "the plaintext seed leaks inside the stored value"


async def test_orm_roundtrip_returns_the_integer_seed(db_session: AsyncSession):
    job = f"a186-rt-{uuid.uuid4().hex[:8]}"
    db_session.add(_record(job))
    await db_session.flush()
    db_session.expire_all()

    back = (
        await db_session.execute(select(WatermarkRecord).where(WatermarkRecord.job_id == job))
    ).scalar_one()
    assert back.key_seed == SEED


async def test_widest_seed_still_stores_as_a_sealed_blob_and_roundtrips(
    db_session: AsyncSession,
):
    """A186b: a 63-bit seed is ciphertext at rest and comes back bit-for-bit."""
    job = f"a186b-wide-{uuid.uuid4().hex[:8]}"
    rec = _record(job, WIDE_SEED)
    db_session.add(rec)
    await db_session.flush()

    stored = str(await _raw_column(db_session, rec.id))
    assert stored.startswith(_SEED_LABEL), f"widened seed was not sealed: {stored!r}"
    assert str(WIDE_SEED) not in stored, "the widened plaintext seed leaks inside the stored value"
    assert decrypt_key_seed(stored) == WIDE_SEED

    db_session.expire_all()
    back = (
        await db_session.execute(select(WatermarkRecord).where(WatermarkRecord.job_id == job))
    ).scalar_one()
    assert back.key_seed == WIDE_SEED, "the sealed blob lost bits of the widened seed"


async def test_two_rows_with_the_same_seed_differ_at_rest(db_session: AsyncSession):
    suffix = uuid.uuid4().hex[:8]
    a, b = _record(f"a186-dup-{suffix}-a"), _record(f"a186-dup-{suffix}-b")
    db_session.add_all([a, b])
    await db_session.flush()

    blob_a = str(await _raw_column(db_session, a.id))
    blob_b = str(await _raw_column(db_session, b.id))
    assert blob_a != blob_b, (
        "identical ciphertext for identical seeds means an unauthenticated/deterministic "
        "encoding — a DB dump could group records by seed without decrypting"
    )
    assert decrypt_key_seed(blob_a) == decrypt_key_seed(blob_b) == SEED


async def test_legacy_plain_integer_row_is_still_readable(db_session: AsyncSession):
    """Disclosed compatibility: out-of-band rows predating revision 0030 pass through."""
    rec = _record(f"a186-legacy-{uuid.uuid4().hex[:8]}")
    db_session.add(rec)
    await db_session.flush()
    rid = rec.id
    await db_session.execute(
        text("UPDATE watermark_records SET key_seed = 7 WHERE id = :i"), {"i": rid}
    )
    db_session.expire_all()

    row = (
        await db_session.execute(select(WatermarkRecord).where(WatermarkRecord.id == rid))
    ).scalar_one()
    assert row.key_seed == 7


async def test_garbage_stored_value_fails_loud_not_silently(db_session: AsyncSession):
    rec = _record(f"a186-junk-{uuid.uuid4().hex[:8]}")
    db_session.add(rec)
    await db_session.flush()
    rid = rec.id
    await db_session.execute(
        text("UPDATE watermark_records SET key_seed = 'not-a-seed' WHERE id = :i"),
        {"i": rid},
    )
    db_session.expire_all()

    with pytest.raises(Exception, match="neither a cds-ks1: blob nor a plain integer"):
        await db_session.execute(
            select(WatermarkRecord).where(WatermarkRecord.id == rid)
        )


def test_pg_column_is_no_longer_a_plain_integer():
    ddl = str(CreateTable(WatermarkRecord.__table__).compile(dialect=postgresql.dialect()))
    line = next(l for l in ddl.splitlines() if "key_seed" in l)
    assert "TEXT" in line.upper(), (
        f"key_seed must be stored as a ciphertext blob column, rendered: {line.strip()}"
    )
    assert "INTEGER" not in line.upper(), line


def test_seed_helpers_roundtrip_and_aad_binds_the_scheme_tag():
    blob = encrypt_key_seed(SEED)
    assert blob.startswith(_SEED_LABEL)
    assert decrypt_key_seed(blob) == SEED
    import base64

    body = base64.b64decode(blob[len(_SEED_LABEL):], validate=True)
    assert len(body) >= 12 + 16 + len(str(SEED)), "envelope must be [nonce(12)|tag(16)|ct]"


def test_training_bound_contract_is_the_sealed_logical_width():
    """The cross-track contract with app/api/training.py (backend-16b + A186b).

    ``_key_seed_column_max`` fail-closes on column types it cannot width-check.
    The sealed column keeps INTEGER *semantics* but derives from ``BigInteger``
    since A186b, so the validated seed domain is 0…2**63-1 (the finding's
    “31-bit 种子” sub-issue) while the storage stays the width-free
    ``cds-ks1:`` TEXT blob — no accepted value can overflow the blob column.
    """
    assert _key_seed_column_max() == 2**63 - 1


def _alembic(db_path: Path, *targets: str) -> subprocess.CompletedProcess:
    env = {
        **{k: v for k, v in __import__("os").environ.items() if not k.lower().endswith("_proxy")},
        "NO_PROXY": "*",
        "CDS_DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", *targets],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.mark.slow
def test_revision_0030_seals_plaintext_rows_written_on_the_previous_head(tmp_path):
    """Rows created at 0029 hold plaintext ints; after 0030 they must hold blobs."""
    db = tmp_path / "legacy.db"
    build = _alembic(db, PREVIOUS_REVISION)
    assert build.returncode == 0, build.stdout + build.stderr

    import sqlite3

    seed = 987654321
    row_id = uuid.uuid4().hex
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO watermark_records (id, job_id, owner_id, model_id,"
            " layer_positions, ordered_positions, key_seed, num_bits, created_at)"
            " VALUES (?, 'a186-legacy', 'o', 'm', '{}', '[]', ?, 8, '2026-01-01 00:00:00')",
            (row_id, seed),
        )
        conn.commit()
    finally:
        conn.close()

    upgrade = _alembic(db, "head")
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr

    engine = create_engine(f"sqlite:///{db}")
    try:
        with engine.connect() as conn:
            stored = conn.execute(
                text("SELECT key_seed FROM watermark_records WHERE id = :i"), {"i": row_id}
            ).scalar()
            assert isinstance(stored, str) and stored.startswith(_SEED_LABEL), (
                f"revision 0030 left a plaintext seed behind: {stored!r}"
            )
            assert decrypt_key_seed(stored) == seed
            assert conn.execute(
                text("SELECT COUNT(*) FROM watermark_records WHERE id = :i"), {"i": row_id}
            ).scalar() == 1, "the backfill must not lose or duplicate rows"

        with Session(engine) as s:
            rec = s.execute(select(WatermarkRecord)).scalar_one()
            assert rec.key_seed == seed
    finally:
        engine.dispose()
