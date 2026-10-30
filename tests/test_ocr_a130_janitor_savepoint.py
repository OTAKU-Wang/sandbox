"""A130: 保留期工头——上报的不是真正删掉的行数，且单表报错后整个会话已废（high）。

`app/services/retention_janitor.py` 两条：
① `removed` 累加的是 SELECT 到的 id 个数而不是 DELETE 的 rowcount ⇒ 并发下有别的副本
   删走同一批行时数字虚高，而 docs/retention.md 要求运维拿这个数字核对；
② 单表 try/except 吞掉异常后不回退到 savepoint 就在同一会话上继续 ⇒ PostgreSQL 一条
   语句报错后事务进入 aborted(25P02)，后面的表、`pg_advisory_unlock`、循环后的
   `retention.purge` 审计写入以及调用方的 `db.commit()` 全部连锁失败（SQLite 也会把会话
   标成 needs-rollback，所以这条在进程内车道就能复现）。

删除口径不因本条放开：`RETENTION_JANITOR_DRY_RUN` 默认 True、`retention_days<=0` 直接
跳过、审计日志与 Merkle 叶子默认永久保留——最后一个测试钉住「干跑时一条 DELETE 都不发」。
"""
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.event import listens_for, remove
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.database import Base
from app.models.alert import AlertRecord
from app.models.audit_log import AuditLog
from app.services import retention_janitor as rj


@pytest_asyncio.fixture()
async def engine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/janitor.db")
    async with eng.begin() as conn:
        await conn.run_sync(
            lambda sc: Base.metadata.create_all(
                sc, tables=[AlertRecord.__table__, AuditLog.__table__], checkfirst=False
            )
        )
    yield eng
    await eng.dispose()


@pytest.fixture()
def statements():
    return []


@pytest.fixture(autouse=True)
def _collect(engine, statements):
    @listens_for(engine.sync_engine, "before_cursor_execute")
    def _seen(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    yield
    remove(engine.sync_engine, "before_cursor_execute", _seen)


def _pin(monkeypatch, *, dry_run: bool, audit=0, alert=0, merkle=0, batch=3):
    settings = get_settings()
    for name, value in {
        "RETENTION_JANITOR_DRY_RUN": dry_run,
        "RETENTION_JANITOR_BATCH": batch,
        "AUDIT_LOG_RETENTION_DAYS": audit,
        "ALERT_RETENTION_DAYS": alert,
        "MERKLE_LEAF_RETENTION_DAYS": merkle,
    }.items():
        monkeypatch.setattr(settings, name, value, raising=False)


async def _seed_alerts(engine, rows: int, prefix: str) -> None:
    stamp = datetime.now(timezone.utc) - timedelta(days=400)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        for n in range(rows):
            session.add(
                AlertRecord(
                    dedup_key=f"{prefix}-{n}", alert_type="t", severity="low",
                    message="m", created_at=stamp,
                )
            )
        await session.commit()


async def _count(engine, model) -> int:
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        return (await session.execute(select(func.count()).select_from(model))).scalar()


@pytest.mark.asyncio
async def test_purge_reports_the_delete_rowcount(engine, monkeypatch):
    await _seed_alerts(engine, rows=5, prefix="a")
    async with engine.begin() as conn:
        await conn.execute(text(
            "CREATE TRIGGER keep_two BEFORE DELETE ON alert_records "
            "WHEN OLD.dedup_key IN ('a-1','a-2') BEGIN SELECT RAISE(IGNORE); END"
        ))
    _pin(monkeypatch, dry_run=False, alert=180)

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        results = await rj.purge_cycle(session)
        await session.commit()

    remaining = await _count(engine, AlertRecord)
    assert results["alert_records"] == 5 - remaining, (
        f"上报的不是 DELETE 真正删掉的行数：报 {results['alert_records']}、"
        f"实删 {5 - remaining}（触发器扮的就是「SELECT 到的行已经不在 / 删不掉」）"
    )
    assert remaining == 2


@pytest.mark.asyncio
async def test_failed_table_is_isolated_in_its_own_savepoint(engine, monkeypatch, statements):
    await _seed_alerts(engine, rows=2, prefix="b")
    _pin(monkeypatch, dry_run=False, alert=180, merkle=180)

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        results = await rj.purge_cycle(session)
        logged = (
            await session.execute(select(AuditLog).where(AuditLog.action == "retention.purge"))
        ).scalars().all()
        remaining = (await session.execute(select(func.count()).select_from(AlertRecord))).scalar()

    assert results["merkle_leaves"] == -1
    assert results["alert_records"] == 2
    assert len(logged) == 1, "循环后的 retention.purge 审计写入被 merkle_leaves 的失败带走了"
    assert remaining == 0, "单表失败后会话已不可用"
    assert any(s.lstrip().upper().startswith("SAVEPOINT") for s in statements)
    assert any("ROLLBACK TO SAVEPOINT" in s.upper() for s in statements)


@pytest.mark.asyncio
async def test_dry_run_emits_no_delete(engine, monkeypatch, statements):
    await _seed_alerts(engine, rows=4, prefix="d")
    _pin(monkeypatch, dry_run=True, alert=180)

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        results = await rj.purge_cycle(session)

    assert results["alert_records"] == 0
    assert await _count(engine, AlertRecord) == 4
    assert not [s for s in statements if s.lstrip().upper().startswith("DELETE")], (
        f"干跑发出了 DELETE：{[s[:48] for s in statements]}"
    )
