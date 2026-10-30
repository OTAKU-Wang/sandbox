"""A186b — ``key_seed`` 熵加宽（31→63 位）的迁移侧契约（``### A186`` 最后一条腿）。

台账记的是「水印的全部安全性 = 这颗 31-bit 种子」。密文落存已在 revision
``0030_ocr_seed_cipher_unique_guards`` 落地并把熵加宽留给下一轮；本轮把
``EncryptedKeySeed`` 的整型基类从 ``Integer`` 换成 ``BigInteger``（逻辑域
0…2**63-1），并新增 revision ``0031_ocr_seed_bigint``。

0031 不写死形状：物理列自 0030 起是无界 ``TEXT``（密文封套），对它做
``ALTER … TYPE bigint`` 会把密文强转成整数（PG 报 cast 错，SQLite batch 把封套清成
0），所以 0031 反射实际列型后分流：无界 TEXT → 校验后不动；遗留整型列（本仓
``create_all`` + ``alembic stamp`` 的库）→ 放宽为 TEXT 并逐行封装；认不出来的列型
→ 中止。本文件锁这三条读数 + 单头。
"""
import os
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect

REPO_ROOT = Path(__file__).resolve().parents[1]
REVISION_0030 = "0030_ocr_seed_cipher_unique_guards"
REVISION = "0031_ocr_seed_bigint"

WIDE_SEED = 2**62 + 12345  # 在 31 位域下会被拒；加宽后必须装得下也读得回


def _alembic(db_path: Path, *targets: str) -> subprocess.CompletedProcess[str]:
    env = {
        **{k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")},
        "NO_PROXY": "*",
        "CDS_DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", *targets],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True,
    )


def _key_seed_ddl(db_path: Path) -> str:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='watermark_records'"
        ).fetchone()
    finally:
        conn.close()
    line = next(l for l in row[0].splitlines() if "key_seed" in l)
    return line.strip().rstrip(",")


def _insert_plaintext_seed(db_path: Path, seed: int) -> str:
    """Write a raw integer seed straight into the column (pre-encryption shape)."""
    row_id = uuid.uuid4().hex
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO watermark_records (id, job_id, owner_id, model_id,"
            " layer_positions, ordered_positions, key_seed, num_bits)"
            " VALUES (?, 'a186b', 'o', 'm', '{}', '[]', ?, 8)",
            (row_id, seed),
        )
        conn.commit()
    finally:
        conn.close()
    return row_id


def test_chain_has_exactly_one_head_and_0031_stays_on_the_linear_trunk():
    """alembic 历史线性只增不改：0031 之后每加一个合法 revision，「头恰等于
    0031」都会红一次——那是把历史快照当契约。正确的钉子是两条：**全链恒单头**
    （只有分叉才会多头）且 **0031 永远在 head 的祖先历史里**（有人改写/抹除
    已发布历史才会红）。2026-10-30 由 0032 落地时的全量红（heads 变
    ['0032_refresh_sessions (head)']）触发的形状修正，断言只强不弱。
    """
    result = _alembic(Path(":memory:"), "heads")
    assert result.returncode == 0, result.stdout + result.stderr
    heads = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
    assert len(heads) == 1, heads
    hist = _alembic(Path(":memory:"), "history")
    assert hist.returncode == 0, hist.stdout + hist.stderr
    assert any(
        ln.strip().startswith(f"{REVISION} ->") or f" -> {REVISION}" in ln
        for ln in hist.stdout.splitlines()
    ), f"{REVISION} 不在 head 的祖先历史里：\n{hist.stdout}"


@pytest.mark.slow
def test_migrated_head_keeps_the_sealed_text_column_and_holds_a_63_bit_seed(tmp_path):
    """正常升级链：0031 是校验过的 no-op，列仍是 TEXT，63 位种子原样往返。"""
    from sqlalchemy.orm import Session

    from app.models.watermark_record import (
        _SEED_LABEL,
        WatermarkRecord,
        decrypt_key_seed,
    )

    db = tmp_path / "head.db"
    assert _alembic(db, "upgrade", "head").returncode == 0

    assert "TEXT" in _key_seed_ddl(db).upper(), _key_seed_ddl(db)

    engine = create_engine(f"sqlite:///{db}")
    try:
        reflected = next(
            c for c in inspect(engine).get_columns("watermark_records")
            if c["name"] == "key_seed"
        )
        assert isinstance(reflected["type"], sa.Text), reflected["type"]

        job = f"a186b-head-{uuid.uuid4().hex[:8]}"
        with Session(engine) as s:
            s.add(WatermarkRecord(
                job_id=job, owner_id="o", model_id="m",
                layer_positions={}, ordered_positions=[],
                key_seed=WIDE_SEED, num_bits=8,
            ))
            s.commit()

        with engine.connect() as conn:
            stored = conn.execute(sa.text(
                "SELECT key_seed FROM watermark_records WHERE job_id = :j"
            ), {"j": job}).scalar()
        assert isinstance(stored, str) and stored.startswith(_SEED_LABEL), (
            f"the 63-bit seed did not reach the DB sealed: {stored!r}"
        )
        assert str(WIDE_SEED) not in stored
        assert decrypt_key_seed(stored) == WIDE_SEED
    finally:
        engine.dispose()


@pytest.mark.slow
def test_revision_0031_widens_and_seals_a_legacy_integer_column(tmp_path):
    """遗留形状（列仍是 INTEGER、种子是明文）：0031 放宽为 TEXT 并封装，不丢行。"""
    from app.models.watermark_record import _SEED_LABEL, decrypt_key_seed

    db = tmp_path / "legacy.db"
    assert _alembic(db, "upgrade", REVISION_0030).returncode == 0

    conn = sqlite3.connect(db)
    try:
        conn.execute("DROP TABLE watermark_records")
        conn.execute(
            "CREATE TABLE watermark_records ("
            " id VARCHAR(32) PRIMARY KEY, job_id VARCHAR(64) NOT NULL,"
            " owner_id VARCHAR(128) NOT NULL, model_id VARCHAR(128) NOT NULL,"
            " layer_positions JSON NOT NULL, ordered_positions JSON NOT NULL,"
            " key_seed INTEGER NOT NULL, num_bits INTEGER NOT NULL)"
        )
        conn.commit()
    finally:
        conn.close()
    row_id = _insert_plaintext_seed(db, WIDE_SEED)

    upgrade = _alembic(db, "upgrade", "head")  # alembic_version == 0030 → only 0031 runs
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr

    engine = create_engine(f"sqlite:///{db}")
    try:
        with engine.connect() as conn:
            stored = conn.execute(sa.text(
                "SELECT key_seed FROM watermark_records WHERE id = :i"
            ), {"i": row_id}).scalar()
            assert isinstance(stored, str) and stored.startswith(_SEED_LABEL), (
                f"revision 0031 left a plaintext/short-column seed behind: {stored!r}"
            )
            assert decrypt_key_seed(stored) == WIDE_SEED, "the widened seed was clipped"
            assert conn.execute(
                sa.text("SELECT COUNT(*) FROM watermark_records")
            ).scalar() == 1, "the widen must not lose or duplicate rows"
        assert "TEXT" in _key_seed_ddl(db).upper(), _key_seed_ddl(db)
    finally:
        engine.dispose()


@pytest.mark.slow
def test_revision_0031_refuses_a_column_type_it_cannot_reason_about(tmp_path):
    """fail-closed：列型认不出来时中止，而不是猜一个「大概装得下」。"""
    db = tmp_path / "weird.db"
    assert _alembic(db, "upgrade", REVISION_0030).returncode == 0

    conn = sqlite3.connect(db)
    try:
        conn.execute("DROP TABLE watermark_records")
        conn.execute(
            "CREATE TABLE watermark_records ("
            " id VARCHAR(32) PRIMARY KEY, job_id VARCHAR(64) NOT NULL,"
            " owner_id VARCHAR(128) NOT NULL, model_id VARCHAR(128) NOT NULL,"
            " layer_positions JSON NOT NULL, ordered_positions JSON NOT NULL,"
            " key_seed FLOAT NOT NULL, num_bits INTEGER NOT NULL)"
        )
        conn.commit()
    finally:
        conn.close()

    upgrade = _alembic(db, "upgrade", "head")
    out = upgrade.stdout + upgrade.stderr
    assert upgrade.returncode != 0, "0031 upgraded a column shape it cannot reason about"
    assert "refusing to guess" in out, out[-2000:]
