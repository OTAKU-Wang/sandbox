"""W5: Alembic baseline migration tests.

- a fresh database reaches head via `alembic upgrade head` and contains every
  live table
- the Alembic chain and Base.metadata.create_all produce identical schemas
  (scripts/verify_schema_parity.py, run as a subprocess)
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_alembic(db_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "CDS_DATABASE_URL": f"sqlite+aiosqlite:///{db_path}"}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True,
    )


def _live_tables() -> set[str]:
    import app.main  # noqa: F401
    from app.core.database import Base

    return set(Base.metadata.tables)


@pytest.mark.slow
def test_alembic_upgrade_head_builds_all_tables(tmp_path):
    db = tmp_path / "fresh.db"
    result = _run_alembic(db, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    engine = create_engine(f"sqlite:///{db}")
    inspector = inspect(engine)
    built = {t for t in inspector.get_table_names() if t != "alembic_version"}
    expected = _live_tables()
    assert built == expected, (
        f"missing={sorted(expected - built)} extra={sorted(built - expected)}"
    )


@pytest.mark.slow
def test_schema_parity_script_exits_zero(tmp_path):
    result = subprocess.run(
        [sys.executable, "scripts/verify_schema_parity.py"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SCHEMA PARITY OK" in result.stdout


@pytest.mark.slow
def test_full_downgrade_clears_database(tmp_path):
    db = tmp_path / "downgrade.db"
    assert _run_alembic(db, "upgrade", "head").returncode == 0
    result = _run_alembic(db, "downgrade", "base")
    assert result.returncode == 0, result.stderr

    engine = create_engine(f"sqlite:///{db}")
    inspector = inspect(engine)
    remaining = {t for t in inspector.get_table_names() if t != "alembic_version"}
    assert remaining == set()
