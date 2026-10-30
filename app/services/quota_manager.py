"""Quota Manager — Redis Lua atomic counters for resource quotas.

Tracks row/byte/API/GPU usage per session with atomic increment-and-check
operations to prevent quota overrun under concurrent access.
"""
import uuid
import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class QuotaType(str, Enum):
    ROWS = "rows"
    BYTES = "bytes"
    API_CALLS = "api_calls"
    GPU_SECONDS = "gpu_seconds"
    DP_EPSILON = "dp_epsilon"
    DURATION_SECONDS = "duration_seconds"


@dataclass
class QuotaLimit:
    """Quota limit definition."""
    quota_type: QuotaType
    max_value: int
    current_value: int = 0


@dataclass
class QuotaCheckResult:
    """Result of a quota check/increment."""
    allowed: bool
    quota_type: QuotaType
    current: int
    limit: int
    remaining: int
    reason: str = ""


class QuotaBackendUnavailable(RuntimeError):
    """A146: 配额后端的读取失败不能被当成「0 用量 / 没限额」。"""


# Lua script: atomic increment-and-check
# KEYS[1] = counter key
# ARGV[1] = max limit
# ARGV[2] = increment amount
# Returns: {new_value, allowed(1/0)}
LUA_INCREMENT_AND_CHECK = """
local current = redis.call('GET', KEYS[1])
if current == false then
    current = 0
else
    current = tonumber(current)
end
local new_value = current + tonumber(ARGV[2])
local limit = tonumber(ARGV[1])
if limit > 0 and new_value > limit then
    return {current, 0}
end
redis.call('SET', KEYS[1], new_value)
return {new_value, 1}
"""


_MAX_PENDING_GATEWAY_DELTAS = 500


class QuotaManager:
    """Manages resource quotas using Redis atomic counters.

    Each quota is stored as a Redis key: `quota:{session_id}:{quota_type}`
    Uses Lua scripting for atomic increment-and-check to prevent race conditions.
    """

    def __init__(self, redis_client=None):
        self._redis = redis_client
        self._lua_script = LUA_INCREMENT_AND_CHECK
        self._local_counters: dict[str, int] = {}  # Fallback for no-Redis
        self._local_limits: dict[str, int] = {}
        self._local_gateway_counters: dict[str, int] = {}
        self._pending_gateway_deltas: list[tuple[str, int]] = []
        self._local_fallback_events = 0

    def quota_health(self) -> dict:
        """A146: 降级必须是可见状态，而不是一行 warning。回写完成前 pending_deltas 不归零。"""
        return {
            "backend": "redis" if self._redis else "in-process",
            "local_fallback_events": self._local_fallback_events,
            "pending_deltas": len(self._pending_gateway_deltas),
            "degraded": bool(self._pending_gateway_deltas),
        }

    def _key(self, session_id: uuid.UUID, quota_type: QuotaType) -> str:
        return f"quota:{session_id}:{quota_type.value}"

    def _limit_key(self, session_id: uuid.UUID, quota_type: QuotaType) -> str:
        return f"quota_limit:{session_id}:{quota_type.value}"

    async def set_limits(self, session_id: uuid.UUID, limits: dict[QuotaType, int]) -> None:
        """Store quota limits in Redis for a session."""
        for qt, limit in limits.items():
            if not isinstance(qt, QuotaType):
                qt = QuotaType(qt)
            key = self._limit_key(session_id, qt)
            self._local_limits[key] = int(limit)
            if self._redis:
                try:
                    await self._redis.set(key, limit, ex=86400 * 7)  # 7 days TTL
                except Exception as e:
                    logger.warning(f"Failed to set quota limit for {qt.value}: {e}")

    async def get_limit(
        self, session_id: uuid.UUID, quota_type: QuotaType, *, strict: bool = False
    ) -> int:
        """Get stored quota limit. Returns 0 if not set.

        A146: 「没读到」和「读失败」不是一回事。strict 用于执法路径（读失败 ⇒ 抛错、
        由调用方 fail-closed）；非 strict 保持旧口径（返回 0），但失败不再静默。
        """
        key = self._limit_key(session_id, quota_type)
        if self._redis:
            try:
                val = await self._redis.get(key)
                return int(val) if val else 0
            except Exception as e:
                if strict:
                    raise QuotaBackendUnavailable(
                        f"quota limit unreadable for {quota_type.value}: {e.__class__.__name__}"
                    ) from e
                logger.error(
                    "[quota][A146] limit read failed for %s (%s) — treated as unset (unlimited) "
                    "for this non-enforcing caller only", key, e.__class__.__name__,
                )
        return self._local_limits.get(key, 0)

    async def check_and_increment(
        self,
        session_id: uuid.UUID,
        quota_type: QuotaType,
        amount: int,
        limit: int = 0,
    ) -> QuotaCheckResult:
        """Atomically check quota and increment if within limit.

        If limit is 0, attempts to read the stored limit from Redis (set by set_limits()).
        If Redis is unavailable, falls back to local in-memory counters
        (not production-safe, but allows development without Redis).
        """
        # Auto-read stored limit when caller doesn't provide one
        if not limit:
            try:
                limit = await self.get_limit(session_id, quota_type, strict=True)
            except QuotaBackendUnavailable as e:
                logger.error("[quota][A146] %s — request refused instead of treated as unlimited", e)
                return QuotaCheckResult(
                    allowed=False, quota_type=quota_type, current=0, limit=0, remaining=0,
                    reason="quota backend unavailable",
                )

        key = self._key(session_id, quota_type)

        if self._redis:
            try:
                result = await self._redis.eval(
                    self._lua_script,
                    keys=[key],
                    args=[limit, amount],
                )
                new_value = int(result[0])
                allowed = int(result[1]) == 1
                return QuotaCheckResult(
                    allowed=allowed,
                    quota_type=quota_type,
                    current=new_value,
                    limit=limit,
                    remaining=max(0, limit - new_value),
                )
            except Exception as e:
                logger.warning(f"Redis quota check failed, falling back to local: {e}")

        # Local fallback
        current = self._local_counters.get(key, 0)
        new_value = current + amount
        allowed = limit <= 0 or new_value <= limit
        if allowed:
            self._local_counters[key] = new_value
        return QuotaCheckResult(
            allowed=allowed,
            quota_type=quota_type,
            current=new_value if allowed else current,
            limit=limit,
            remaining=max(0, limit - (new_value if allowed else current)),
        )

    async def get_usage(
        self, session_id: uuid.UUID, quota_type: QuotaType
    ) -> int:
        """Get current usage for a quota type."""
        key = self._key(session_id, quota_type)

        if self._redis:
            try:
                val = await self._redis.get(key)
                return int(val) if val else 0
            except Exception:
                pass

        return self._local_counters.get(key, 0)

    async def reset(
        self,
        session_id: uuid.UUID,
        contract_limits: dict | None = None,
    ) -> None:
        """Reset all quotas for a session (e.g., on session start).

        Args:
            session_id: The session to reset quotas for.
            contract_limits: Optional contract limits to pre-fill quotas.
                Keys: 'max_output_rows', 'dp_epsilon_budget', 'max_duration_seconds',
                'max_requests_per_second'.
        """
        for qt in QuotaType:
            key = self._key(session_id, qt)
            if self._redis:
                try:
                    await self._redis.delete(key)
                except Exception:
                    pass
            self._local_counters.pop(key, None)
            self._local_limits.pop(self._limit_key(session_id, qt), None)

        # Pre-fill from contract limits if provided (#151)
        if contract_limits:
            limit_map = {
                QuotaType.ROWS: contract_limits.get("max_output_rows", 10000),
                QuotaType.DP_EPSILON: int(float(contract_limits.get("dp_epsilon_budget", 10)) * 10000),
                QuotaType.DURATION_SECONDS: contract_limits.get("max_duration_seconds", 3600),
                QuotaType.API_CALLS: contract_limits.get("max_requests_per_second", 100),
            }
            await self.set_limits(
                session_id,
                {qt: int(limit) for qt, limit in limit_map.items() if limit is not None},
            )

    async def check_batch(
        self,
        session_id: uuid.UUID,
        quotas: dict[QuotaType, tuple[int, int]],  # {type: (amount, limit)}
    ) -> dict[QuotaType, QuotaCheckResult]:
        """Check multiple quotas atomically. All must pass or none are consumed."""
        results = {}
        all_allowed = True

        # First pass: check all
        for qt, (amount, limit) in quotas.items():
            key = self._key(session_id, qt)
            if self._redis:
                try:
                    result = await self._redis.eval(
                        self._lua_script,
                        keys=[key],
                        args=[limit, 0],  # Check only, don't increment
                    )
                    current = int(result[0])
                    would_exceed = limit > 0 and (current + amount) > limit
                    results[qt] = QuotaCheckResult(
                        allowed=not would_exceed,
                        quota_type=qt,
                        current=current,
                        limit=limit,
                        remaining=max(0, limit - current),
                    )
                    if would_exceed:
                        all_allowed = False
                    continue
                except Exception:
                    pass

            # Local fallback check
            current = self._local_counters.get(key, 0)
            would_exceed = limit > 0 and (current + amount) > limit
            results[qt] = QuotaCheckResult(
                allowed=not would_exceed,
                quota_type=qt,
                current=current,
                limit=limit,
                remaining=max(0, limit - current),
            )
            if would_exceed:
                all_allowed = False

        # Second pass: increment all if all allowed
        if all_allowed:
            for qt, (amount, limit) in quotas.items():
                key = self._key(session_id, qt)
                if self._redis:
                    try:
                        await self._redis.eval(
                            self._lua_script,
                            keys=[key],
                            args=[limit, amount],
                        )
                    except Exception:
                        pass
                else:
                    self._local_counters[key] = self._local_counters.get(key, 0) + amount

        return results

    # === Gateway daily quota (per app_id, auto-expiring at midnight UTC) ===

    def _gateway_key(self, app_id: str, quota_type: str) -> str:
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        return f"gwquota:{app_id}:{today}:{quota_type}"

    def _redis_usable(self) -> bool:
        """A146: 单例在 main.py 接线前拿到的是个未 await 的协程对象——它不是后端。

        当成「有后端」会让每次调用都走异常→本地计数，并永远不再迁回。
        """
        return self._redis is not None and hasattr(self._redis, "pipeline")

    @staticmethod
    def _daily_ttl() -> int:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0)
        return int((end_of_day - now).total_seconds()) + 1

    def _queue_pending_gateway_delta(self, key: str, amount: int) -> None:
        self._pending_gateway_deltas.append((key, amount))
        if len(self._pending_gateway_deltas) > _MAX_PENDING_GATEWAY_DELTAS:
            dropped = self._pending_gateway_deltas[:len(self._pending_gateway_deltas) - _MAX_PENDING_GATEWAY_DELTAS]
            self._pending_gateway_deltas = self._pending_gateway_deltas[-_MAX_PENDING_GATEWAY_DELTAS:]
            logger.error(
                "[quota][A146] pending gateway deltas overflowed — %d increment(s) will never reach "
                "Redis and are excluded from the authoritative daily counter", len(dropped),
            )

    async def _replay_pending_gateway_deltas(self) -> None:
        """A146: 降级期间的本地增量在 Redis 恢复后回写，不然配额永远卡在本地。"""
        if not self._pending_gateway_deltas or not self._redis_usable():
            return
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        pending, self._pending_gateway_deltas = self._pending_gateway_deltas, []
        fresh = [(k, a) for k, a in pending if k.split(":")[-2] == today]
        stale = len(pending) - len(fresh)
        if stale:
            logger.error(
                "[quota][A146] dropped %d pending gateway delta(s) belonging to a past UTC day — "
                "they are not reflected in any daily counter", stale,
            )
        if not fresh:
            return
        try:
            pipe = self._redis.pipeline()
            for key, amount in fresh:
                pipe.incrby(key, amount)
                pipe.ttl(key)
            responses = await pipe.execute()
            for i, (key, _amount) in enumerate(fresh):
                if int(responses[2 * i + 1]) == -1:
                    await self._redis.expire(key, self._daily_ttl())
            logger.error(
                "[quota][A146] replayed %d pending gateway delta(s) into Redis after recovery", len(fresh),
            )
        except Exception as e:
            self._pending_gateway_deltas = fresh + self._pending_gateway_deltas
            logger.error(
                "[quota][A146] replay of pending gateway deltas failed (%s) — %d still pending",
                e.__class__.__name__, len(self._pending_gateway_deltas),
            )

    async def check_gateway_quota(
        self,
        app_id: str,
        rows_increment: int = 0,
        bytes_increment: int = 0,
        rows_limit: int = 0,
        bytes_limit: int = 0,
    ) -> tuple[bool, str]:
        """Check and increment daily gateway quota for an app.

        Uses the same atomic increment-and-check Lua primitive as session quotas so a
        rejected request writes nothing (A146: 先加后校会让被拒流量抬高日计数器).
        Returns (allowed, reason).
        """
        if not self._redis_usable():
            if self._redis is not None:
                logger.error(
                    "[quota][A146] gateway quota backend is not usable (%s) — serving from "
                    "in-process counters; multi-replica limits are NOT enforced",
                    self._redis.__class__.__name__,
                )
            return self._check_gateway_quota_local(
                app_id,
                rows_increment=rows_increment,
                bytes_increment=bytes_increment,
                rows_limit=rows_limit,
                bytes_limit=bytes_limit,
            )

        try:
            await self._replay_pending_gateway_deltas()

            pipe = self._redis.pipeline()
            tracked: list[tuple[str, str, int, int]] = []

            if rows_limit > 0 and rows_increment > 0:
                rows_key = self._gateway_key(app_id, "rows")
                pipe.eval(self._lua_script, 1, rows_key, rows_limit, rows_increment)
                pipe.ttl(rows_key)
                tracked.append(("row", rows_key, rows_limit, rows_increment))

            if bytes_limit > 0 and bytes_increment > 0:
                bytes_key = self._gateway_key(app_id, "bytes")
                pipe.eval(self._lua_script, 1, bytes_key, bytes_limit, bytes_increment)
                pipe.ttl(bytes_key)
                tracked.append(("byte", bytes_key, bytes_limit, bytes_increment))

            results = await pipe.execute() if tracked else []

            for i, (label, key, limit, increment) in enumerate(tracked):
                verdict = results[2 * i]
                current = int(verdict[0])
                allowed = int(verdict[1]) == 1
                if allowed and int(results[2 * i + 1]) == -1:
                    await self._redis.expire(key, self._daily_ttl())
                if not allowed:
                    return False, f"daily {label} quota exceeded ({current + increment}/{limit})"

            return True, "within quota"

        except Exception as e:
            self._local_fallback_events += 1
            logger.error(
                "[quota][A146] Redis gateway quota failed (%d event(s), %s) — this request was "
                "counted in-process only and is queued for replay into Redis",
                self._local_fallback_events, e.__class__.__name__,
            )
            return self._check_gateway_quota_local(
                app_id,
                rows_increment=rows_increment,
                bytes_increment=bytes_increment,
                rows_limit=rows_limit,
                bytes_limit=bytes_limit,
                record_pending=True,
            )

    def _check_gateway_quota_local(
        self,
        app_id: str,
        *,
        rows_increment: int = 0,
        bytes_increment: int = 0,
        rows_limit: int = 0,
        bytes_limit: int = 0,
        record_pending: bool = False,
    ) -> tuple[bool, str]:
        """Single-process gateway quota fallback used when Redis is unavailable."""
        if rows_increment > 0:
            rows_key = self._gateway_key(app_id, "rows")
            projected_rows = self._local_gateway_counters.get(rows_key, 0) + rows_increment
            if rows_limit > 0 and projected_rows > rows_limit:
                return False, f"daily row quota exceeded ({projected_rows}/{rows_limit})"
            self._local_gateway_counters[rows_key] = projected_rows
            if record_pending:
                self._queue_pending_gateway_delta(rows_key, rows_increment)

        if bytes_increment > 0:
            bytes_key = self._gateway_key(app_id, "bytes")
            projected_bytes = self._local_gateway_counters.get(bytes_key, 0) + bytes_increment
            if bytes_limit > 0 and projected_bytes > bytes_limit:
                return False, f"daily byte quota exceeded ({projected_bytes}/{bytes_limit})"
            self._local_gateway_counters[bytes_key] = projected_bytes
            if record_pending:
                self._queue_pending_gateway_delta(bytes_key, bytes_increment)

        return True, "within local quota"

    async def get_gateway_usage(self, app_id: str, *, strict: bool = False) -> dict:
        """Get current daily gateway usage for an app.

        A146: strict callers must fail closed on an unreadable usage instead of seeing 0;
        the non-strict shape adds ``unavailable`` only when it is true.
        """
        result = {"rows": 0, "bytes": 0}
        if not self._redis_usable():
            result["rows"] = self._local_gateway_counters.get(self._gateway_key(app_id, "rows"), 0)
            result["bytes"] = self._local_gateway_counters.get(self._gateway_key(app_id, "bytes"), 0)
            return result
        try:
            rows_key = self._gateway_key(app_id, "rows")
            bytes_key = self._gateway_key(app_id, "bytes")
            rows_val, bytes_val = await self._redis.mget(rows_key, bytes_key)
            result["rows"] = int(rows_val) if rows_val else 0
            result["bytes"] = int(bytes_val) if bytes_val else 0
        except Exception as e:
            if strict:
                raise QuotaBackendUnavailable(
                    f"gateway usage unreadable: {e.__class__.__name__}"
                ) from e
            logger.error(
                "[quota][A146] gateway usage read failed (%s) — caller cannot tell 0 from unknown",
                e.__class__.__name__,
            )
            result["unavailable"] = True
        return result


# Singleton (redis_client to be injected at startup)
quota_manager = QuotaManager()
