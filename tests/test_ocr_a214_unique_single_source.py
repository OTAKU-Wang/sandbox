"""A214 (E2-8/E2-11) — ``certificates.serial_number`` / ``field_visibility_configs.product_id``
的唯一性必须有且只有一个模式来源：命名唯一索引。

0000 基线在这两列上既建了表级 ``UNIQUE`` 约束（``sa.UniqueConstraint(...)``），
又另建了同名唯一索引（``ix_certificates_serial`` / ``ix_field_visibility_product``），
模型列还各自带 ``unique=True``（⇒ create_all 库里的第三份声明）。本半边把唯一性
收敛到命名唯一索引一个来源（与 A124/A171/A214-mpc 同口径），并由 revision 0030
在 PostgreSQL 上 drop 掉基线带入的 UNIQUE 约束；升级前查重，存量重复即中止
（A214 既定策略，同 0028 的 mpc_key_shares：重复=数据损坏，不裁决、不删行）。
"""
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.schema import CreateTable, UniqueConstraint

from app.models.certificate import Certificate
from app.models.field_exposure import FieldVisibilityConfig

REPO_ROOT = Path(__file__).resolve().parents[1]
PREVIOUS_REVISION = "0029_ocr_size_bigint_op_lease"
REVISION_FILE = REPO_ROOT / "alembic" / "versions" / "0030_ocr_seed_cipher_unique_guards.py"

_CASES = (
    (Certificate, "serial_number", "ix_certificates_serial", "certificates"),
    (FieldVisibilityConfig, "product_id", "ix_field_visibility_product", "field_visibility_configs"),
)


def _unique_constraints_over(table, column: str):
    return [
        c
        for c in table.constraints
        if isinstance(c, UniqueConstraint) and [x.name for x in c.columns] == [column]
    ]


def _unique_indexes_over(table, column: str):
    return [
        i
        for i in table.indexes
        if i.unique and [c.name for c in i.columns] == [column]
    ]


@pytest.mark.parametrize("model,column,index_name,_table", _CASES, ids=["cert", "visibility"])
def test_uniqueness_has_exactly_one_source_in_the_model(model, column, index_name, _table):
    table = model.__table__
    assert not _unique_constraints_over(table, column), (
        f"{_table}.{column} declares a UNIQUE constraint on top of the named unique "
        f"index {index_name} — two schema sources for one invariant is the E2-8/E2-11 "
        "duplicate pair this finding lists"
    )
    indexes = _unique_indexes_over(table, column)
    assert len(indexes) == 1, f"expected exactly one unique index over {column}, got {indexes}"
    assert indexes[0].name == index_name


@pytest.mark.parametrize("model,column,index_name,_table", _CASES, ids=["cert", "visibility"])
def test_create_all_ddl_carries_no_inline_unique_on_the_column(model, column, index_name, _table):
    for dialect, label in ((postgresql.dialect(), "pg"), (sqlite.dialect(), "sqlite")):
        ddl = str(CreateTable(model.__table__).compile(dialect=dialect))
        assert f"UNIQUE ({column})" not in ddl and f"UNIQUE ({column}, " not in ddl, (
            f"create_all on {label} would create a second anonymous UNIQUE constraint "
            f"over {_table}.{column} beside {index_name}:\n{ddl}"
        )


def _fresh(model, column: str):
    """A valid row of either model with a fresh unique value in ``column``."""
    if model is Certificate:
        return Certificate(
            user_id=uuid.uuid4(),
            serial_number="SER-" + uuid.uuid4().hex[:12],
            subject="CN=a186", issuer="CN=a186-ca",
            public_key="pub", certificate_pem="pem",
            not_before=datetime.now(timezone.utc) - timedelta(days=1),
            not_after=datetime.now(timezone.utc) + timedelta(days=1),
        )
    return FieldVisibilityConfig(product_id=uuid.uuid4())


@pytest.mark.parametrize("model,column,index_name,_table", _CASES, ids=["cert", "visibility"])
async def test_the_named_unique_index_still_enforces_uniqueness(model, column, index_name, _table, db_session):
    first = _fresh(model, column)
    db_session.add(first)
    await db_session.flush()

    dup = _fresh(model, column)
    setattr(dup, column, getattr(first, column))
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(dup)
            await db_session.flush()


def test_revision_0030_drops_the_duplicate_pg_constraints():
    """Source-level lock: on PostgreSQL the 0000 UNIQUE constraints must be dropped.

    SQLite cannot reflect or drop an inline table UNIQUE, and the parity gate never
    sees it there — the drop only exists in the revision's PostgreSQL branch, so the
    revision text itself is the only machine-checkable surface (same discipline as
    tests/test_kms_volume_keys.py pinning the Go field names).
    """
    assert REVISION_FILE.exists(), "revision 0030 is missing"
    src = REVISION_FILE.read_text(encoding="utf-8")
    assert "drop_constraint" in src, "0030 must drop the duplicate UNIQUE constraints"
    assert 'type_="unique"' in src or "type_='unique'" in src
    assert "certificates_serial_number_key" in src, "0030 must name the certificates constraint"
    assert "field_visibility_configs_product_id_key" in src, "0030 must name the visibility constraint"


def _alembic(db_path: Path, *targets: str):
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


def _corrupt_unique_state(conn, table: str, column: str, index_name: str) -> None:
    """Recreate ``table`` without ANY uniqueness (named index dropped, inline
    table-level UNIQUE stripped from the rebuilt DDL): the out-of-band
    corruption state the 0030 guard defends against. The tables are empty at
    this point (freshly migrated), so a drop/recreate loses nothing.
    """
    conn.execute(f"DROP INDEX {index_name}")
    ddl = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()[0]
    kept = [
        line for line in ddl.splitlines()
        if f"UNIQUE ({column})" not in line
    ]
    for i in range(len(kept) - 1, -1, -1):
        if kept[i].strip() in (")", ""):
            continue
        kept[i] = kept[i].rstrip().rstrip(",")
        break
    conn.execute(f"DROP TABLE {table}")
    conn.execute("\n".join(kept))


def _seed_duplicate_serials(db_path: Path) -> None:
    """Corruption scenario the guard defends against: uniqueness is gone
    (index dropped AND pre-constraint inline UNIQUE stripped) and duplicates
    arrive. Rows are written straight into the pre-0030 schema; FKs are off in
    raw sqlite3."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        _corrupt_unique_state(conn, "certificates", "serial_number", "ix_certificates_serial")
        for i in range(2):
            conn.execute(
                "INSERT INTO certificates (id, user_id, cert_type, status, serial_number,"
                " subject, issuer, public_key, certificate_pem, is_ca, not_before, not_after)"
                " VALUES (?, ?, 'identity', 'active', 'DUP-SERIAL-001', 'CN=a', 'CN=b',"
                " 'pub', 'pem', 0, '2026-01-01 00:00:00', '2027-01-01 00:00:00')",
                (uuid.uuid4().hex, uuid.uuid4().hex),
            )
        conn.execute(
            "INSERT INTO certificates (id, user_id, cert_type, status, serial_number,"
            " subject, issuer, public_key, certificate_pem, is_ca, not_before, not_after)"
            " VALUES (?, ?, 'identity', 'active', 'SOLO-SERIAL-002', 'CN=c', 'CN=b',"
            " 'pub', 'pem', 0, '2026-01-01 00:00:00', '2027-01-01 00:00:00')",
            (uuid.uuid4().hex, uuid.uuid4().hex),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_duplicate_products(db_path: Path) -> None:
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        _corrupt_unique_state(
            conn, "field_visibility_configs", "product_id", "ix_field_visibility_product"
        )
        product = uuid.uuid4().hex
        for _ in range(2):
            conn.execute(
                "INSERT INTO field_visibility_configs (id, product_id, field_rules,"
                " default_sensitivity) VALUES (?, ?, '{}', 'internal')",
                (uuid.uuid4().hex, product),
            )
        conn.commit()
    finally:
        conn.close()


@pytest.mark.slow
def test_upgrade_aborts_on_duplicate_serial_numbers_and_keeps_every_row(tmp_path):
    db = tmp_path / "dupcert.db"
    build = _alembic(db, PREVIOUS_REVISION)
    assert build.returncode == 0, build.stdout + build.stderr
    _seed_duplicate_serials(db)

    upgrade = _alembic(db, "head")
    assert upgrade.returncode != 0, "0030 must refuse to upgrade over duplicate serials"
    err = upgrade.stderr + upgrade.stdout
    assert "certificates" in err and "serial_number" in err, (
        f"the abort message must name the offending table.column, got: {err[-400:]}"
    )
    assert "ix_certificates_serial" in err, "the abort message must name the owner index"

    engine = create_engine(f"sqlite:///{db}")
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM certificates")).scalar() == 3, (
                "the refusal must not delete anything — duplicate resolution is the operator's"
            )
    finally:
        engine.dispose()


@pytest.mark.slow
def test_upgrade_aborts_on_duplicate_product_ids(tmp_path):
    db = tmp_path / "dupvis.db"
    build = _alembic(db, PREVIOUS_REVISION)
    assert build.returncode == 0, build.stdout + build.stderr
    _seed_duplicate_products(db)

    upgrade = _alembic(db, "head")
    assert upgrade.returncode != 0, "0030 must refuse to upgrade over duplicate product_ids"
    err = upgrade.stderr + upgrade.stdout
    assert "field_visibility_configs" in err and "product_id" in err, err[-400:]


@pytest.mark.slow
def test_upgrade_passes_and_indexes_stay_unique_on_a_clean_database(tmp_path):
    db = tmp_path / "clean.db"
    build = _alembic(db, PREVIOUS_REVISION)
    assert build.returncode == 0, build.stdout + build.stderr

    upgrade = _alembic(db, "head")
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr

    engine = create_engine(f"sqlite:///{db}")
    try:
        with engine.connect() as conn:
            for name in ("ix_certificates_serial", "ix_field_visibility_product"):
                row = conn.execute(
                    text("SELECT sql FROM sqlite_master WHERE type='index' AND name=:n"),
                    {"n": name},
                ).scalar()
                assert row and "UNIQUE" in row.upper(), f"{name} lost its UNIQUE after upgrade: {row!r}"
    finally:
        engine.dispose()
