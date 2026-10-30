"""Sandbox Manager — orchestrates sandbox lifecycle with resource enforcement.

Integrates SandboxRuntime with database-backed session management.
Enforces resource limits per sandbox level and handles session timeouts.
Thread-safe via asyncio.Lock for multi-tenant concurrent access.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta

from app.core.errors import CDSError
from app.models.sandbox_session import SandboxLevel, SessionStatus

logger = logging.getLogger(__name__)

# Resource limits per sandbox level
RESOURCE_LIMITS = {
    SandboxLevel.L0.value: {
        "cpu_cores": 1,
        "memory_mb": 256,
        "disk_mb": 512,
        "max_timeout_seconds": 3600,  # 1h
        "network": False,
    },
    SandboxLevel.L1.value: {
        "cpu_cores": 4,
        "memory_mb": 4096,
        "disk_mb": 10240,
        "max_timeout_seconds": 86400,  # 24h
        "network": False,
    },
    SandboxLevel.L2.value: {
        "cpu_cores": 2,
        "memory_mb": 1024,
        "disk_mb": 5120,
        "max_timeout_seconds": 14400,  # 4h
        "network": False,
    },
    SandboxLevel.L3.value: {
        "cpu_cores": 1,
        "memory_mb": 512,
        "disk_mb": 1024,
        "max_timeout_seconds": 7200,  # 2h
        "network": False,
    },
    SandboxLevel.K8S.value: {
        "cpu_cores": 1,
        "memory_mb": 512,
        "disk_mb": 1024,
        "max_timeout_seconds": 7200,  # 2h
        "network": False,
    },
}


def get_resource_limits(sandbox_level: str) -> dict:
    """Get resource limits for a sandbox level.

    A108: 必须返回拷贝——旧实现直接递出 `RESOURCE_LIMITS` 里的字典本体，
    任何一处就地写（`limits["cpu_cores"] = …`）会污染全进程默认值。
    """
    return dict(RESOURCE_LIMITS.get(sandbox_level, RESOURCE_LIMITS[SandboxLevel.L3.value]))


# A108: 只认这三个键（其余键照旧忽略，不得借别名绕过钳制）
_VALIDATABLE_LIMIT_KEYS = ("cpu_cores", "memory_mb", "disk_mb")


def _require_positive_int(key: str, value: object) -> int:
    """A108: 资源限额必须是正整数。

    0/负数会被 `min()` 原样采纳（会话被钳成不可用），字符串/小数在 `min()`
    里抛 TypeError 把会话创建打成 500——用户可稳定触发。改成 422。

    整值的浮点（`2.0`）必须接受：SS-15 流程的 wire 契约就是浮点
    （`app/schemas/flow.py` 把 `cpu_cores` 定成 `float | None` 并在自己的校验里
    要求 `.is_integer()`），到这里的值已被 pydantic  coercion 成 float；拒掉它
    会把合约驱动的 dev-session 创建打成 422。非整浮点（`1.5`）仍拒。
    """
    if isinstance(value, bool) or value is None:
        raise CDSError(
            f"resource limit {key} must be a positive integer",
            detail={"field": key, "received": type(value).__name__},
            code="VALIDATION_ERROR",
            http_status=422,
        )
    if isinstance(value, float):
        if not value.is_integer():
            raise CDSError(
                f"resource limit {key} must be a positive integer",
                detail={"field": key, "received": value},
                code="VALIDATION_ERROR",
                http_status=422,
            )
        value = int(value)
    if not isinstance(value, int):
        raise CDSError(
            f"resource limit {key} must be a positive integer",
            detail={"field": key, "received": type(value).__name__},
            code="VALIDATION_ERROR",
            http_status=422,
        )
    if value <= 0:
        raise CDSError(
            f"resource limit {key} must be greater than zero",
            detail={"field": key, "received": value},
            code="VALIDATION_ERROR",
            http_status=422,
        )
    return value


def validate_resource_limits(sandbox_level: str, requested_limits: dict | None) -> dict:
    """Validate and clamp requested resource limits to level maximums.

    Returns the effective resource limits dict (always a fresh copy).
    """
    level_limits = get_resource_limits(sandbox_level)
    if not requested_limits:
        return level_limits

    effective = level_limits
    for key in _VALIDATABLE_LIMIT_KEYS:
        if key in requested_limits:
            requested = _require_positive_int(key, requested_limits[key])
            effective[key] = min(requested, level_limits[key])
    return effective


def is_session_expired(session) -> bool:
    """Check if a session has exceeded its timeout."""
    if session.status in (
        SessionStatus.COMPLETED.value,
        SessionStatus.TERMINATED.value,
        SessionStatus.FAILED.value,
        SessionStatus.REVOKED.value,
    ):
        return False
    if not session.created_at:
        return False

    # SQLite DateTime(timezone=True) round-trips as naive — normalise to UTC
    # so the comparison never fails with a naive/aware mismatch.
    created = session.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    elapsed = datetime.now(timezone.utc) - created
    # Round 39: POST /{id}/refreshes grants extra wall-clock seconds beyond
    # the base timeout; total extension is capped by the refresh endpoint.
    # int-coerce defensively: session-like test doubles (MagicMock) auto-create
    # attributes, so getattr's default never fires for them.
    try:
        extended = int(getattr(session, "extended_seconds", 0) or 0)
    except (TypeError, ValueError):
        extended = 0
    timeout = timedelta(seconds=int(session.timeout_seconds) + extended)
    return elapsed > timeout


def attestation_required_for_level(sandbox_level: str) -> bool:
    """Levels that must present TEE attestation before key distribution (P0-9)."""
    return sandbox_level in {SandboxLevel.L1.value, SandboxLevel.L2.value}


def attestation_from_provision(provision_result: dict) -> bytes | None:
    """Extract the raw attestation quote bytes from a provision result."""
    quote = (provision_result or {}).get("attestation_quote")
    if isinstance(quote, bytes):
        return quote
    if isinstance(quote, str) and quote:
        return quote.encode("utf-8")
    return None


def attestation_from_session(session) -> bytes | None:
    """Extract the stored attestation quote for a sandbox session."""
    limits = getattr(session, "resource_limits", None) or {}
    record = limits.get("attestation") if isinstance(limits, dict) else None
    if not isinstance(record, dict):
        return None
    quote = record.get("quote")
    if isinstance(quote, bytes):
        return quote
    if isinstance(quote, str) and quote:
        return quote.encode("utf-8")
    return None


sandbox_manager = None
_manager_lock = asyncio.Lock()

# Per-tenant resource quota tracking
# Key: user_id, Value: dict with aggregated resource usage
# W3: the local dict stays the synchronous read path for the existing
# callers; every mutation is written through to the Redis hash
# ``cds:tenant_quota:{user_id}`` so usage survives restarts and is visible
# to replicas. restore_tenant_quotas() seeds the dict back from Redis.
_tenant_quotas: dict[str, dict] = {}
# A109: 尚未合成进 Redis 的**增量**（不是快照）。Redis 侧一律用 HINCRBY 叠加，
# 所以多副本并发写只会把用量相加，谁也不会拿自己的本地快照覆盖对方的用量；
# 写失败时增量留在里面对着次 flush 重试，不丢账。
_pending_deltas: dict[str, dict[str, int]] = {}
_USAGE_FIELDS = ("used_cpu_cores", "used_memory_mb", "used_disk_mb")
TENANT_QUOTA_KEY_PREFIX = "cds:tenant_quota:"
TENANT_DEFAULTS = {
    "max_sessions": 5,
    "max_cpu_cores": 8,
    "max_memory_mb": 8192,
    "max_disk_mb": 20480,
}

_QUOTA_INT_FIELDS = (
    "max_sessions",
    "max_cpu_cores",
    "max_memory_mb",
    "max_disk_mb",
    "used_cpu_cores",
    "used_memory_mb",
    "used_disk_mb",
)


def get_tenant_quota(user_id: str) -> dict:
    """Get or create tenant resource quota."""
    if user_id not in _tenant_quotas:
        _tenant_quotas[user_id] = TENANT_DEFAULTS.copy()
    return _tenant_quotas[user_id]


def _record_delta(user_id: str, delta: dict[str, int]) -> None:
    """累加一笔待写回的增量。

    净零也要记：update+release 相互抵后仍需一次 HINCRBY 把字段落到 Redis，
    否则读方看到的是一个不存在的键（“从未发生过”）而不是“用量为 0”。
    """
    bucket = _pending_deltas.setdefault(user_id, {})
    for field, inc in delta.items():
        bucket[field] = bucket.get(field, 0) + int(inc)


def _apply_local_delta(user_id: str, delta: dict[str, int]) -> None:
    """把增量应用到本进程视图（用于 Redis 已生效或 Redis 不可用的降级路径）。"""
    quota = get_tenant_quota(user_id)
    for field, inc in delta.items():
        quota[field] = int(quota.get(field, 0) or 0) + inc


def _quota_exceeds_reason(used_cpu: int, used_mem: int, used_disk: int, quota: dict) -> str:
    """纯函数：用**已经含本次请求量**的用量对限额，超了就返回原因。"""
    if used_cpu > quota["max_cpu_cores"]:
        return f"CPU quota exceeded ({used_cpu}/{quota['max_cpu_cores']} cores)"
    if used_mem > quota["max_memory_mb"]:
        return f"Memory quota exceeded ({used_mem}/{quota['max_memory_mb']} MB)"
    if used_disk > quota["max_disk_mb"]:
        return f"Disk quota exceeded ({used_disk}/{quota['max_disk_mb']} MB)"
    return ""


async def _persist_tenant_quota(user_id: str) -> None:
    """把该租户的待写增量用 HINCRBY 合成进 Redis（A109）。

    - 只写增量，不写绝对快照：两个副本的用量是合成而不是覆盖；
    - 没有待写增量 ⇒ 直接返回，**绝不 `delete`**（旧实现会在本地条目被
      管理员删除后把其他副本刚写进去的用量整键抹掉）；
    - 失败 ⇒ 增量原封留着，下一次 `flush_quota_persistence()` 重试。
    """
    delta = _pending_deltas.get(user_id)
    if not delta:
        return
    try:
        from app.core.redis import get_redis

        redis = await get_redis()
        key = TENANT_QUOTA_KEY_PREFIX + user_id
        for field, inc in list(delta.items()):
            await redis.hincrby(key, field, inc)
        # 只有真的写进去了才清账
        _pending_deltas.pop(user_id, None)
    except Exception as e:
        logger.warning(
            "Tenant quota delta flush failed for %s: %s — 增量保留，下次 flush 重试",
            user_id,
            e,
        )


async def _overwrite_tenant_quota(user_id: str) -> None:
    """绝对覆写（仅用于从 DB 重建的真值重建路径，不参与多副本合成）。"""
    try:
        from app.core.redis import get_redis

        redis = await get_redis()
        quota = _tenant_quotas.get(user_id)
        if quota is None:
            _pending_deltas.pop(user_id, None)
            return
        await redis.hset(
            TENANT_QUOTA_KEY_PREFIX + user_id,
            mapping={k: str(v) for k, v in quota.items()},
        )
        _pending_deltas.pop(user_id, None)
    except Exception as e:
        logger.warning("Tenant quota overwrite failed for %s: %s", user_id, e)


def _schedule_quota_persist(user_id: str) -> asyncio.Task | None:
    """Fire-and-forget write-through; tracked in _persist_tasks for flushing."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    task = loop.create_task(_persist_tenant_quota(user_id))
    _persist_tasks.add(task)
    task.add_done_callback(_persist_tasks.discard)
    return task


_persist_tasks: set[asyncio.Task] = set()


async def flush_quota_persistence() -> None:
    """Await pending write-through tasks, then **retry** any delta left behind.

    仅 await 任务不够：写失败那一笔的增量只是留在 `_pending_deltas` 里，
    不重试就等于静默丢账（A109）。
    """
    if _persist_tasks:
        await asyncio.gather(*list(_persist_tasks), return_exceptions=True)
    for user_id in list(_pending_deltas):
        await _persist_tenant_quota(user_id)


def update_tenant_usage(user_id: str, cpu_cores: int = 0, memory_mb: int = 0, disk_mb: int = 0):
    """Update aggregated resource usage for a tenant."""
    delta = {
        "used_cpu_cores": int(cpu_cores or 0),
        "used_memory_mb": int(memory_mb or 0),
        "used_disk_mb": int(disk_mb or 0),
    }
    _apply_local_delta(user_id, delta)
    _record_delta(user_id, delta)
    _schedule_quota_persist(user_id)


def check_tenant_quota(user_id: str, cpu_cores: int, memory_mb: int, disk_mb: int) -> tuple[bool, str]:
    """Check if a tenant has enough quota for a new session (local, non-atomic).

    Returns (allowed, reason). 这是快速预筛；**跨副本真值请用
    `await try_reserve_tenant_usage(...)`**，本函数与 `update_tenant_usage` 分开
    调用构成 check-then-act，多副本会双双通过（A109）。
    """
    quota = get_tenant_quota(user_id)
    used_cpu = int(quota.get("used_cpu_cores", 0) or 0) + cpu_cores
    used_mem = int(quota.get("used_memory_mb", 0) or 0) + memory_mb
    used_disk = int(quota.get("used_disk_mb", 0) or 0) + disk_mb
    reason = _quota_exceeds_reason(used_cpu, used_mem, used_disk, quota)
    return (reason == ""), reason


def release_tenant_usage(user_id: str, cpu_cores: int = 0, memory_mb: int = 0, disk_mb: int = 0):
    """Release aggregated resource usage for a tenant (on session terminate).

    超量释放（别的副本已经扣过 / 重复 terminate）钳到 0 是诚实的，但不能
    **静默**：钳了多少必须说，否则账本自己也不可信（A109）。
    """
    quota = get_tenant_quota(user_id)
    requested = {
        "used_cpu_cores": int(cpu_cores or 0),
        "used_memory_mb": int(memory_mb or 0),
        "used_disk_mb": int(disk_mb or 0),
    }
    applied: dict[str, int] = {}
    for field, want in requested.items():
        have = int(quota.get(field, 0) or 0)
        actual = min(want, have)
        quota[field] = have - actual
        if want > have:
            logger.warning(
                "Tenant %s over-released %s: requested %s but only %s tracked — clamped to 0",
                user_id, field, want, have,
            )
        applied[field] = -actual
    _record_delta(user_id, applied)
    _schedule_quota_persist(user_id)


async def try_reserve_tenant_usage(
    user_id: str, cpu_cores: int = 0, memory_mb: int = 0, disk_mb: int = 0
) -> tuple[bool, str]:
    """原子预留租户用量（A109：取代 `check_tenant_quota` + `update_tenant_usage`）。

    语义：
    1. 本地已能判出超额 ⇒ 直接拒，**绝不先改 Redis**；
    2. 否则先 HINCRBY 把自己的增量写进去，再读回合成后的真值（含其他副本
       刚写的那笔）；超阈就 HINCRBY 负数回滚自己那一笔并拒；
    3. Redis 不可用 ⇒ 诚实地降级为进程内原子预留（单进程仍是原子的），但会
       留下 WARNING，并把增量记入 `_pending_deltas` 等 Redis 恢复后补写。

    返回 (预留成功?, 原因)。
    """
    delta = {
        "used_cpu_cores": int(cpu_cores or 0),
        "used_memory_mb": int(memory_mb or 0),
        "used_disk_mb": int(disk_mb or 0),
    }
    quota = get_tenant_quota(user_id)
    local_reason = _quota_exceeds_reason(
        int(quota.get("used_cpu_cores", 0) or 0) + delta["used_cpu_cores"],
        int(quota.get("used_memory_mb", 0) or 0) + delta["used_memory_mb"],
        int(quota.get("used_disk_mb", 0) or 0) + delta["used_disk_mb"],
        quota,
    )
    if local_reason:
        return False, local_reason

    key = TENANT_QUOTA_KEY_PREFIX + user_id
    reserved_in_redis: list[str] = []
    try:
        from app.core.redis import get_redis

        redis = await get_redis()
        # 先把之前没写进去的账补上，否则读回的真值少算自己旧账
        if _pending_deltas.get(user_id):
            await _persist_tenant_quota(user_id)
            if _pending_deltas.get(user_id):
                raise RuntimeError("pending quota deltas could not be composed")
        for field, inc in delta.items():
            await redis.hincrby(key, field, inc)
            reserved_in_redis.append(field)
        data = await redis.hmget(key, *_USAGE_FIELDS)
        used = {f: int(float(v)) if v is not None else 0 for f, v in zip(_USAGE_FIELDS, data)}
        reason = _quota_exceeds_reason(
            used["used_cpu_cores"], used["used_memory_mb"], used["used_disk_mb"], quota,
        )
        if reason:
            try:
                for field in reserved_in_redis:
                    await redis.hincrby(key, field, -delta[field])
            except Exception as rollback_err:
                logger.warning(
                    "Tenant %s 超额回滚失败（%s）：Redis 里残留 %s 的预留未扣回，"
                    "需要靠 restore_tenant_quotas/reconcile 对账，本次请求仍按拒绝处理",
                    user_id, rollback_err, ",".join(reserved_in_redis),
                )
            return False, reason
        # Redis 已含这一笔 ⇒ 只同步本地，不能再记 pending（否则重复计数）
        _apply_local_delta(user_id, delta)
        return True, ""
    except Exception as e:
        logger.warning(
            "Tenant quota Redis reserve unavailable for %s (%s) — 降级为进程内原子预留，"
            "增量将在 Redis 恢复后补写",
            user_id, e,
        )
        local_reason = _quota_exceeds_reason(
            int(quota.get("used_cpu_cores", 0) or 0) + delta["used_cpu_cores"],
            int(quota.get("used_memory_mb", 0) or 0) + delta["used_memory_mb"],
            int(quota.get("used_disk_mb", 0) or 0) + delta["used_disk_mb"],
            quota,
        )
        if local_reason:
            return False, local_reason
        _apply_local_delta(user_id, delta)
        # Redis 侧可能已经落了前几个字段（逐字段 HINCRBY 中途失败）：补写只能记未落的那部分，
        # 否则恢复后会把已落的字段重复计一次账。
        remaining = {f: inc for f, inc in delta.items() if f not in reserved_in_redis}
        if remaining:
            _record_delta(user_id, remaining)
            _schedule_quota_persist(user_id)
        return True, ""


async def restore_tenant_quotas() -> int:
    """Load persisted per-tenant usage from Redis into the local cache.

    Restart-recovery path: returns how many tenants were restored.
    """
    from app.core.redis import get_redis

    redis = await get_redis()
    restored = 0
    async for key in redis.scan_iter(match=f"{TENANT_QUOTA_KEY_PREFIX}*"):
        user_id = key[len(TENANT_QUOTA_KEY_PREFIX):]
        data = await redis.hgetall(key)
        if not data:
            continue
        quota = TENANT_DEFAULTS.copy()
        for field in _QUOTA_INT_FIELDS:
            if field in data:
                try:
                    quota[field] = int(float(data[field]))
                except (TypeError, ValueError):
                    pass
        _tenant_quotas[user_id] = quota
        restored += 1
    return restored


async def rebuild_tenant_quotas(db) -> int:
    """Recompute per-tenant usage from non-terminal sessions in the DB.

    Redis-loss recovery path: the DB is the source of truth for what is
    currently running. Returns the number of tenants with usage.
    """
    from sqlalchemy import select

    from app.models.sandbox_session import SandboxSession, SessionStatus

    active_states = [
        SessionStatus.PENDING.value,
        SessionStatus.PROVISIONING.value,
        SessionStatus.RUNNING.value,
        SessionStatus.SUSPENDED.value,
    ]
    result = await db.execute(
        select(SandboxSession).where(SandboxSession.status.in_(active_states))
    )
    sessions = result.scalars().all()

    usage: dict[str, dict[str, int]] = {}
    for session in sessions:
        user_key = str(session.user_id)
        limits = session.resource_limits if isinstance(session.resource_limits, dict) else {}
        bucket = usage.setdefault(user_key, {"used_cpu_cores": 0, "used_memory_mb": 0, "used_disk_mb": 0})
        bucket["used_cpu_cores"] += int(limits.get("cpu_cores", 0) or 0)
        bucket["used_memory_mb"] += int(limits.get("memory_mb", 0) or 0)
        bucket["used_disk_mb"] += int(limits.get("disk_mb", 0) or 0)

    touched = set(usage)
    touched.update(_tenant_quotas)
    for user_id in touched:
        quota = get_tenant_quota(user_id)
        for field in ("used_cpu_cores", "used_memory_mb", "used_disk_mb"):
            quota[field] = usage.get(user_id, {}).get(field, 0)
        await _overwrite_tenant_quota(user_id)
    return len(usage)


async def get_sandbox_manager():
    """Lazy-init sandbox manager with thread-safe singleton."""
    global sandbox_manager
    if sandbox_manager is None:
        async with _manager_lock:
            if sandbox_manager is None:  # Double-check after acquiring lock
                from app.services.sandbox_runtime import SandboxRuntime
                sandbox_manager = SandboxRuntime()
    return sandbox_manager
