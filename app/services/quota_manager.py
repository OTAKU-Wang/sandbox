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

    async def get_limit(self, session_id: uuid.UUID, quota_type: QuotaType) -> int:
        """Get stored quota limit from Redis. Returns 0 if not set."""
        key = self._limit_key(session_id, quota_type)
        if self._redis:
            try:
                val = await self._redis.get(key)
                return int(val) if val else 0
            except Exception:
                pass
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
            limit = await self.get_limit(session_id, quota_type)

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

    async def check_gateway_quota(
        self,
        app_id: str,
        rows_increment: int = 0,
        bytes_increment: int = 0,
        rows_limit: int = 0,
        bytes_limit: int = 0,
    ) -> tuple[bool, str]:
        """Check and increment daily gateway quota for an app.

        Uses Redis keys with automatic TTL (expire at end of UTC day).
        Returns (allowed, reason).
        """
        if not self._redis:
            return self._check_gateway_quota_local(
                app_id,
                rows_increment=rows_increment,
                bytes_increment=bytes_increment,
                rows_limit=rows_limit,
                bytes_limit=bytes_limit,
            )

        try:
            pipe = self._redis.pipeline()

            # Check rows quota
            if rows_limit > 0 and rows_increment > 0:
                rows_key = self._gateway_key(app_id, "rows")
                pipe.incrby(rows_key, rows_increment)
                # Set TTL to end of UTC day if key is new
                pipe.ttl(rows_key)

            # Check bytes quota
            if bytes_limit > 0 and bytes_increment > 0:
                bytes_key = self._gateway_key(app_id, "bytes")
                pipe.incrby(bytes_key, bytes_increment)
                pipe.ttl(bytes_key)

            results = await pipe.execute()

            idx = 0
            if rows_limit > 0 and rows_increment > 0:
                rows_current = int(results[idx])
                idx += 1
                rows_ttl = int(results[idx])
                idx += 1
                # Auto-expire: set TTL to end of UTC day if key is new (TTL = -1)
                if rows_ttl == -1:
                    from datetime import datetime, timezone
                    now = datetime.now(timezone.utc)
                    end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0)
                    ttl_seconds = int((end_of_day - now).total_seconds()) + 1
                    await self._redis.expire(self._gateway_key(app_id, "rows"), ttl_seconds)
                if rows_current > rows_limit:
                    return False, f"daily row quota exceeded ({rows_current}/{rows_limit})"

            if bytes_limit > 0 and bytes_increment > 0:
                bytes_current = int(results[idx])
                idx += 1
                bytes_ttl = int(results[idx])
                idx += 1
                if bytes_ttl == -1:
                    from datetime import datetime, timezone
                    now = datetime.now(timezone.utc)
                    end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0)
                    ttl_seconds = int((end_of_day - now).total_seconds()) + 1
                    await self._redis.expire(self._gateway_key(app_id, "bytes"), ttl_seconds)
                if bytes_current > bytes_limit:
                    return False, f"daily byte quota exceeded ({bytes_current}/{bytes_limit})"

            return True, "within quota"

        except Exception as e:
            logger.warning(f"[quota] Gateway quota check failed: {e}")
            return self._check_gateway_quota_local(
                app_id,
                rows_increment=rows_increment,
                bytes_increment=bytes_increment,
                rows_limit=rows_limit,
                bytes_limit=bytes_limit,
            )

    def _check_gateway_quota_local(
        self,
        app_id: str,
        *,
        rows_increment: int = 0,
        bytes_increment: int = 0,
        rows_limit: int = 0,
        bytes_limit: int = 0,
    ) -> tuple[bool, str]:
        """Single-process gateway quota fallback used when Redis is unavailable."""
        if rows_increment > 0:
            rows_key = self._gateway_key(app_id, "rows")
            new_rows = self._local_gateway_counters.get(rows_key, 0) + rows_increment
            self._local_gateway_counters[rows_key] = new_rows
            if rows_limit > 0 and new_rows > rows_limit:
                return False, f"daily row quota exceeded ({new_rows}/{rows_limit})"

        if bytes_increment > 0:
            bytes_key = self._gateway_key(app_id, "bytes")
            new_bytes = self._local_gateway_counters.get(bytes_key, 0) + bytes_increment
            self._local_gateway_counters[bytes_key] = new_bytes
            if bytes_limit > 0 and new_bytes > bytes_limit:
                return False, f"daily byte quota exceeded ({new_bytes}/{bytes_limit})"

        return True, "within local quota"

    async def get_gateway_usage(self, app_id: str) -> dict:
        """Get current daily gateway usage for an app."""
        result = {"rows": 0, "bytes": 0}
        if not self._redis:
            result["rows"] = self._local_gateway_counters.get(self._gateway_key(app_id, "rows"), 0)
            result["bytes"] = self._local_gateway_counters.get(self._gateway_key(app_id, "bytes"), 0)
            return result
        try:
            rows_key = self._gateway_key(app_id, "rows")
            bytes_key = self._gateway_key(app_id, "bytes")
            rows_val, bytes_val = await self._redis.mget(rows_key, bytes_key)
            result["rows"] = int(rows_val) if rows_val else 0
            result["bytes"] = int(bytes_val) if bytes_val else 0
        except Exception:
            pass
        return result


# Singleton (redis_client to be injected at startup)
quota_manager = QuotaManager()
