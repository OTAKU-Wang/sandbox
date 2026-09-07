"""W3: tenant quota persistence — Redis write-through, restart restore, DB rebuild.

Covers docs/cubesandbox-parity-plan.md W3 test matrix:
- update/release write through to the Redis hash and survive a (simulated)
  process restart via restore_tenant_quotas()
- rebuild_tenant_quotas() recomputes usage from non-terminal sessions in the
  DB (the Redis-loss recovery path)
"""
import uuid

import pytest
import pytest_asyncio
import fakeredis.aioredis

from app.models.sandbox_session import SandboxSession, SessionStatus
from app.models.user import User
from app.services import sandbox_manager
from app.services.sandbox_manager import (
    TENANT_QUOTA_KEY_PREFIX,
    check_tenant_quota,
    flush_quota_persistence,
    get_tenant_quota,
    rebuild_tenant_quotas,
    release_tenant_usage,
    restore_tenant_quotas,
    update_tenant_usage,
)


@pytest_asyncio.fixture(autouse=True)
async def _fake_redis_and_clean_state(monkeypatch):
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    import app.core.redis as redis_module

    monkeypatch.setattr(redis_module, "_redis_client", fake)
    sandbox_manager._tenant_quotas.clear()
    yield fake
    sandbox_manager._tenant_quotas.clear()


@pytest.mark.asyncio
async def test_update_and_release_write_through_to_redis(_fake_redis_and_clean_state):
    update_tenant_usage("tenant-a", cpu_cores=2, memory_mb=1024, disk_mb=4096)
    await flush_quota_persistence()
    stored = await _fake_redis_and_clean_state.hgetall(TENANT_QUOTA_KEY_PREFIX + "tenant-a")
    assert stored["used_cpu_cores"] == "2"
    assert stored["used_memory_mb"] == "1024"
    assert stored["used_disk_mb"] == "4096"

    release_tenant_usage("tenant-a", cpu_cores=1, memory_mb=512, disk_mb=2048)
    await flush_quota_persistence()
    stored = await _fake_redis_and_clean_state.hgetall(TENANT_QUOTA_KEY_PREFIX + "tenant-a")
    assert stored["used_cpu_cores"] == "1"
    assert stored["used_memory_mb"] == "512"
    assert stored["used_disk_mb"] == "2048"


@pytest.mark.asyncio
async def test_restore_tenant_quotas_recovers_after_restart(_fake_redis_and_clean_state):
    update_tenant_usage("tenant-a", cpu_cores=4, memory_mb=4096, disk_mb=8192)
    await flush_quota_persistence()

    sandbox_manager._tenant_quotas.clear()

    restored = await restore_tenant_quotas()
    assert restored >= 1
    quota = get_tenant_quota("tenant-a")
    assert quota["used_cpu_cores"] == 4
    assert quota["used_memory_mb"] == 4096
    assert quota["used_disk_mb"] == 8192
    assert quota["max_cpu_cores"] == sandbox_manager.TENANT_DEFAULTS["max_cpu_cores"]
    allowed, _ = check_tenant_quota("tenant-a", cpu_cores=8, memory_mb=0, disk_mb=0)
    assert allowed is False


@pytest.mark.asyncio
async def test_rebuild_tenant_quotas_from_db(db_session, _fake_redis_and_clean_state):
    unique = uuid.uuid4().hex[:8]
    user = User(
        username=f"quota_{unique}",
        email=f"quota_{unique}@example.com",
        hashed_password="x",
        role="data_provider",
    )
    db_session.add(user)
    await db_session.flush()
    user_id = str(user.id)

    for _ in range(2):
        db_session.add(SandboxSession(
            id=uuid.uuid4(),
            user_id=user.id,
            data_product_id=uuid.uuid4(),
            sandbox_level="L3",
            sandbox_mode="query",
            status=SessionStatus.RUNNING.value,
            resource_limits={"cpu_cores": 1, "memory_mb": 512, "disk_mb": 1024},
        ))
    await db_session.flush()

    update_tenant_usage(user_id, cpu_cores=7, memory_mb=7000, disk_mb=7000)
    await flush_quota_persistence()
    sandbox_manager._tenant_quotas.clear()
    await _fake_redis_and_clean_state.flushall()

    rebuilt = await rebuild_tenant_quotas(db_session)
    assert rebuilt >= 1
    quota = get_tenant_quota(user_id)
    assert quota["used_cpu_cores"] == 2
    assert quota["used_memory_mb"] == 1024
    assert quota["used_disk_mb"] == 2048
    await flush_quota_persistence()
    stored = await _fake_redis_and_clean_state.hgetall(TENANT_QUOTA_KEY_PREFIX + user_id)
    assert stored["used_cpu_cores"] == "2"


@pytest.mark.asyncio
async def test_rebuild_ignores_terminal_sessions(db_session, _fake_redis_and_clean_state):
    unique = uuid.uuid4().hex[:8]
    user = User(
        username=f"quota_t_{unique}",
        email=f"quota_t_{unique}@example.com",
        hashed_password="x",
        role="data_provider",
    )
    db_session.add(user)
    await db_session.flush()

    db_session.add(SandboxSession(
        id=uuid.uuid4(),
        user_id=user.id,
        data_product_id=uuid.uuid4(),
        sandbox_level="L3",
        sandbox_mode="query",
        status=SessionStatus.TERMINATED.value,
        resource_limits={"cpu_cores": 4, "memory_mb": 4096, "disk_mb": 8192},
    ))
    await db_session.flush()

    await rebuild_tenant_quotas(db_session)
    quota = get_tenant_quota(str(user.id))
    assert quota.get("used_cpu_cores", 0) == 0
    assert quota.get("used_memory_mb", 0) == 0
