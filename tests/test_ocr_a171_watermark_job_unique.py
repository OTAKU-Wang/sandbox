"""A171 — one watermark record per training job, enforced by the schema.

`app/api/training.py` reads `WatermarkRecord` by `job_id` with
`scalar_one_or_none()` (the embed pre-check and the verify endpoint), so a
second row for the same job turns both endpoints into a permanent 500. The
`job_id` index was plain, so the pre-check was a check-then-act with nothing
behind it. Revision 0028 clears historical duplicates and rebuilds the index
as UNIQUE; that dedupe branch is exercised here by building the pre-0028
schema with the real Alembic chain and upgrading over it.
"""
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.watermark_record import WatermarkRecord

INDEX_NAME = "ix_watermark_records_job_id"
REPO_ROOT = Path(__file__).resolve().parents[1]
PREVIOUS_REVISION = "0027_connector_key_prefix_width"


def _job_index():
    return next((i for i in WatermarkRecord.__table__.indexes if i.name == INDEX_NAME), None)


def _record(job_id: str) -> WatermarkRecord:
    return WatermarkRecord(
        job_id=job_id,
        owner_id="owner-1",
        model_id="model-1",
        layer_positions={"fc.weight": [0, 1]},
        ordered_positions=[["fc.weight", 0], ["fc.weight", 1]],
        key_seed=7,
        num_bits=16,
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


def _insert_pre_revision_rows(db_path: Path, rows: list[tuple[str, str, str]]) -> None:
    """rows: (id, job_id, created_at) — written straight into the pre-0028 schema."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        for row_id, job_id, created_at in rows:
            conn.execute(
                "INSERT INTO watermark_records (id, job_id, owner_id, model_id,"
                " layer_positions, ordered_positions, key_seed, num_bits, created_at)"
                " VALUES (?, ?, 'o', 'm', '{}', '[]', 1, 8, ?)",
                (row_id, job_id, created_at),
            )
        conn.commit()
    finally:
        conn.close()


def _latest_row_id(db_path: Path, job_id: str) -> str:
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT id FROM watermark_records WHERE job_id=? ORDER BY created_at DESC", (job_id,)
        ).fetchone()[0]
    finally:
        conn.close()


def test_job_id_index_is_unique():
    index = _job_index()
    assert index is not None
    assert index.unique is True, (
        "job_id has a plain index while training.py reads it with scalar_one_or_none(): "
        "a duplicate row makes the embed and verify endpoints raise forever"
    )
    assert [c.name for c in index.columns] == ["job_id"]


def test_no_second_index_was_added_for_job_id():
    job_id_indexes = [i for i in WatermarkRecord.__table__.indexes if [c.name for c in i.columns] == ["job_id"]]
    assert len(job_id_indexes) == 1, f"duplicate index objects on job_id: {job_id_indexes}"


async def test_second_row_for_the_same_job_is_rejected(db_session: AsyncSession):
    job = f"job-{uuid.uuid4().hex[:8]}"
    db_session.add(_record(job))
    await db_session.flush()

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(_record(job))
            await db_session.flush()


async def test_different_jobs_still_coexist(db_session: AsyncSession):
    suffix = uuid.uuid4().hex[:8]
    a, b = _record(f"job-a-{suffix}"), _record(f"job-b-{suffix}")
    db_session.add_all([a, b])
    await db_session.flush()
    assert a.id != b.id


@pytest.mark.slow
def test_revision_dedupes_legacy_duplicates_and_makes_job_id_unique(tmp_path):
    db = tmp_path / "legacy.db"
    build = _alembic(db, PREVIOUS_REVISION)
    assert build.returncode == 0, build.stdout + build.stderr

    loser_id = uuid.uuid4().hex
    _insert_pre_revision_rows(
        db,
        [
            (uuid.uuid4().hex, "job-dup", "2026-01-01 10:00:00"),
            (loser_id, "job-dup", "2026-01-02 10:00:00"),
            (uuid.uuid4().hex, "job-solo", "2026-01-01 09:00:00"),
        ],
    )
    assert _latest_row_id(db, "job-dup") == loser_id

    upgrade = _alembic(db, "head")
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr

    engine = create_engine(f"sqlite:///{db}")
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT id, job_id, created_at FROM watermark_records ORDER BY job_id")
            ).all()
            assert len(rows) == 2, f"expected one surviving row per job, got {rows}"
            survivor = next(r for r in rows if r.job_id == "job-dup")
            assert survivor[2] == "2026-01-01 10:00:00", "the later racing row must be the one dropped"
            assert loser_id not in {r[0] for r in rows}
            assert conn.execute(
                text("SELECT COUNT(*) FROM watermark_records WHERE job_id='job-solo'")
            ).scalar() == 1, "a job without a duplicate must not be touched"

            index_sql = conn.execute(
                text("SELECT sql FROM sqlite_master WHERE type='index' AND name=:n"), {"n": INDEX_NAME}
            ).scalar()
            assert index_sql and "UNIQUE" in index_sql.upper(), index_sql

            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO watermark_records (id, job_id, owner_id, model_id,"
                        " layer_positions, ordered_positions, key_seed, num_bits, created_at)"
                        " VALUES ('ffffffffffffffffffffffffffffffff', 'job-solo', 'o', 'm',"
                        " '{}', '[]', 1, 8, '2026-02-01 00:00:00')"
                    )
                )
    finally:
        engine.dispose()
