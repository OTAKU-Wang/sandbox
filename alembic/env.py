"""Alembic env.py — async migration support for CDS sandbox."""
import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Import all models so Alembic can detect them.
# W5: importing app.main pulls in every router -> service -> model module,
# which is exactly the import surface Base.metadata.create_all sees in
# app/main.py. Keeping both sides on the same import set guarantees the
# baseline migration and create_all never drift (verify_schema_parity.py
# asserts it in CI).
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.main  # noqa: F401,E402

from app.core.database import Base
from app.core.config import get_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL.replace("%", "%%"))

# SQLite stores sa.UUID columns with NUMERIC affinity, so type comparison
# against a migrated SQLite database produces mass false positives. CI sets
# ALEMBIC_COMPARE_TYPES=0 for its SQLite check (table/column/index drift is
# still detected); real type comparison happens against PostgreSQL (W17
# environment acceptance).
#
# A217: the old parse (`== "1"`) silently DISABLED type comparison for the
# values people actually type — `true` / `yes` / `on` were all treated as
# off, i.e. a mis-set flag quietly downgraded the gate. Explicit boolean
# parsing now, and an unrecognized value FAILS LOUD (ValueError) instead of
# silently picking a side: a typo must never turn into a silent false green.
# Unset keeps the historical default: comparison ON.
_COMPARE_TRUTHY = frozenset({"1", "true", "yes", "on"})
_COMPARE_FALSY = frozenset({"0", "false", "no", "off"})


def _parse_compare_types(raw: str | None) -> bool:
    """Parse ALEMBIC_COMPARE_TYPES into a bool; unrecognized values raise."""
    if raw is None or not raw.strip():
        return True
    token = raw.strip().lower()
    if token in _COMPARE_TRUTHY:
        return True
    if token in _COMPARE_FALSY:
        return False
    raise ValueError(
        f"ALEMBIC_COMPARE_TYPES={raw!r} is not a recognized boolean; "
        f"use one of {sorted(_COMPARE_TRUTHY)} (comparison ON), "
        f"{sorted(_COMPARE_FALSY)} (comparison OFF), or leave it unset "
        "(defaults ON) — refusing to guess (A217 fail-closed)"
    )


COMPARE_TYPES = _parse_compare_types(os.environ.get("ALEMBIC_COMPARE_TYPES"))


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=COMPARE_TYPES,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=COMPARE_TYPES)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
