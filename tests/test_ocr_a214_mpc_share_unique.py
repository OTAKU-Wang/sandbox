"""A214 (E2-37) — one Shamir share per (key_id, share_index).

`mpc_key_shares` had no UNIQUE(key_id, share_index), so a double split of one
key can park two different y-values on the same x-coordinate: the Lagrange
denominator goes to zero on reconstruction and, worse, `share_reconstruct`
computes the denominator only over the shares it was handed, so a subset can
silently rebuild the WRONG key. Revision 0028 adds the unique index and
refuses to upgrade while duplicates exist, because silently picking one
custodian's y-value would fabricate a key that looks recoverable.
"""
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mpc_key import MPCKeyRecord, MPCKeyShareRecord

REPO_ROOT = Path(__file__).resolve().parents[1]
PREVIOUS_REVISION = "0027_connector_key_prefix_width"
UNIQUE_INDEX = "uq_mpc_key_shares_key_index"
OLD_INDEX = "ix_mpc_shares_key"


def _index_over(columns: list[str]):
    return next(
        (i for i in MPCKeyShareRecord.__table__.indexes
         if [c.name for c in i.columns] == columns),
        None,
    )


def _alembic(db_path: Path, *targets: str) -> subprocess.CompletedProcess:
    env = {
        **{k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")},
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


async def _make_key(db: AsyncSession) -> str:
    key_id = f"key-{uuid.uuid4().hex[:10]}"
    db.add(MPCKeyRecord(key_id=key_id, algorithm="sm4", threshold=2, total_shares=3, status="active"))
    await db.flush()
    return key_id


async def _share(db: AsyncSession, key_id: str, index: int, value: bytes) -> MPCKeyShareRecord:
    record = MPCKeyShareRecord(
        share_id=f"{key_id}-{index}-{uuid.uuid4().hex[:6]}",
        key_id=key_id,
        share_index=index,
        share_value=value,
        holder_id="custodian-a",
    )
    db.add(record)
    return record


def test_unique_index_covers_key_id_and_share_index():
    index = _index_over(["key_id", "share_index"])
    assert index is not None, (
        "mpc_key_shares has no index over (key_id, share_index): two rows for one x-coordinate "
        "make reconstruction raise or rebuild the wrong key"
    )
    assert index.unique is True, f"{index.name} exists but is not UNIQUE"
    assert index.name == UNIQUE_INDEX


def test_single_column_index_no_longer_duplicates_it():
    assert _index_over(["key_id"]) is None, (
        f"{OLD_INDEX} is covered by the leading column of {UNIQUE_INDEX} — "
        "keeping both is the write-amplification pattern A214 lists as E2-8/E2-9/E2-11"
    )


async def test_a_second_share_for_the_same_index_is_rejected(db_session: AsyncSession):
    key_id = await _make_key(db_session)
    await _share(db_session, key_id, 1, b"y-one")
    await db_session.flush()

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await _share(db_session, key_id, 1, b"y-two")
            await db_session.flush()


async def test_a_full_split_still_persists(db_session: AsyncSession):
    key_id = await _make_key(db_session)
    for index in (1, 2, 3):
        await _share(db_session, key_id, index, bytes([index]))
    await db_session.flush()

    rows = (
        await db_session.execute(select(MPCKeyShareRecord).where(MPCKeyShareRecord.key_id == key_id))
    ).scalars().all()
    assert sorted(r.share_index for r in rows) == [1, 2, 3]


async def test_the_same_index_is_allowed_for_a_different_key(db_session: AsyncSession):
    first, second = await _make_key(db_session), await _make_key(db_session)
    await _share(db_session, first, 1, b"a")
    await _share(db_session, second, 1, b"b")
    await db_session.flush()
    assert first != second


@pytest.mark.slow
def test_upgrade_refuses_to_guess_when_duplicates_already_exist(tmp_path):
    db = tmp_path / "dup_shares.db"
    build = _alembic(db, PREVIOUS_REVISION)
    assert build.returncode == 0, build.stdout + build.stderr

    key_id = f"key-{uuid.uuid4().hex[:10]}"
    import sqlite3

    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO mpc_keys (key_id, algorithm, threshold, total_shares, status)"
            " VALUES (?, 'sm4', 2, 3, 'active')",
            (key_id,),
        )
        for share_id, value in (("s-1", "e1"), ("s-2", "e2")):
            conn.execute(
                "INSERT INTO mpc_key_shares (share_id, key_id, share_index, share_value, holder_id)"
                " VALUES (?, ?, 1, ?, 'custodian')",
                (share_id, key_id, value.encode()),
            )
        conn.commit()
    finally:
        conn.close()

    upgrade = _alembic(db, "head")
    assert upgrade.returncode != 0, "a corrupted share set must stop the upgrade, not be silently deduped"
    output = upgrade.stdout + upgrade.stderr
    assert "mpc_key_shares" in output and "share_index" in output, output[-2000:]

    engine = create_engine(f"sqlite:///{db}")
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM mpc_key_shares")).scalar() == 2, (
                "the refusal must not delete custodian key material on the way out"
            )
    finally:
        engine.dispose()
