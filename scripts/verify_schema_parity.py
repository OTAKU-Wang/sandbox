"""W5: schema parity verification — Alembic chain vs create_all.

Builds two throwaway SQLite databases:
  A: `alembic upgrade head` from scratch
  B: `Base.metadata.create_all` (the dev/test bootstrap path)
then compares table names and per-table column names with the SQLAlchemy
inspector. Exit code 1 on any drift — CI treats this as a migration failure.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _schema_map(db_path: str) -> dict[str, set[str]]:
    from sqlalchemy import create_engine, inspect

    engine = create_engine(f"sqlite:///{db_path}")
    inspector = inspect(engine)
    return {
        table: {col["name"] for col in inspector.get_columns(table)}
        for table in inspector.get_table_names()
        if table != "alembic_version"
    }


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        migrated = os.path.join(tmp, "migrated.db")
        created = os.path.join(tmp, "created.db")

        env = {**os.environ, "CDS_DATABASE_URL": f"sqlite+aiosqlite:///{migrated}"}
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(result.stdout)
            print(result.stderr)
            print("PARITY FAIL: alembic upgrade head did not succeed")
            return 1

        import app.main  # noqa: F401 — full model import surface (same as create_all)
        from app.core.database import Base
        from sqlalchemy import create_engine

        engine = create_engine(f"sqlite:///{created}")
        Base.metadata.create_all(engine)
        engine.dispose()

        migrated_map = _schema_map(migrated)
        created_map = _schema_map(created)

        ok = True
        missing_in_migration = sorted(set(created_map) - set(migrated_map))
        extra_in_migration = sorted(set(migrated_map) - set(created_map))
        if missing_in_migration:
            ok = False
            print(f"PARITY FAIL: tables missing from Alembic chain: {missing_in_migration}")
        if extra_in_migration:
            ok = False
            print(f"PARITY FAIL: extra tables in Alembic chain: {extra_in_migration}")

        for table in sorted(set(migrated_map) & set(created_map)):
            only_migration = sorted(migrated_map[table] - created_map[table])
            only_created = sorted(created_map[table] - migrated_map[table])
            if only_migration or only_created:
                ok = False
                print(f"PARITY FAIL: {table} columns differ — "
                      f"only-in-migration={only_migration}, only-in-create_all={only_created}")

        if ok:
            print(f"SCHEMA PARITY OK: {len(migrated_map)} tables, columns identical")
            return 0
        return 1


if __name__ == "__main__":
    sys.exit(main())
