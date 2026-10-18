"""TTL cache backed by Redis.

Drop-in replacement for the in-memory cache. Uses Redis key expiration
for TTL enforcement — no manual eviction needed.
"""
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_KEY_PREFIX = "cache:"


def _escape_glob(pattern: str) -> str:
    """Neutralise Redis glob metacharacters in a caller-supplied key fragment."""
    return pattern.replace("\\", "\\\\").replace("*", "\\*").replace("?", "\\?").replace("[", "\\[")


async def _get_redis():
    from app.core.redis import get_redis
    return await get_redis()


async def cache_get(key: str) -> Any | None:
    """Get value from cache if not expired."""
    try:
        redis = await _get_redis()
        data = await redis.get(f"{_KEY_PREFIX}{key}")
        if data is not None:
            return json.loads(data)
    except Exception as e:
        logger.error(f"cache_get failed for {key}: {e}")
    return None


async def cache_set(key: str, value: Any, ttl_seconds: int = 60) -> None:
    """Set value in cache with TTL.

    Serialisation runs outside the best-effort guard: an unserialisable value is
    a caller bug and must surface, while a Redis outage stays a silent miss.
    Swallowing both together shows up as a cache that never hits (A27).
    """
    payload = json.dumps(value, ensure_ascii=False)
    try:
        redis = await _get_redis()
        await redis.setex(f"{_KEY_PREFIX}{key}", ttl_seconds, payload)
    except Exception as e:
        logger.error(f"cache_set failed for {key}: {e}")


async def cache_delete(key: str) -> None:
    """Delete a specific cache key."""
    try:
        redis = await _get_redis()
        await redis.delete(f"{_KEY_PREFIX}{key}")
    except Exception as e:
        logger.error(f"cache_delete failed for {key}: {e}")


async def cache_clear(pattern: str = "") -> int:
    """Clear cache entries matching prefix. Empty pattern clears all cache keys."""
    try:
        redis = await _get_redis()
        search = f"{_KEY_PREFIX}{_escape_glob(pattern)}*" if pattern else f"{_KEY_PREFIX}*"
        count = 0
        cursor = 0
        while True:
            cursor, keys = await redis.scan(cursor, match=search, count=100)
            if keys:
                count += await redis.delete(*keys)
            if cursor == 0:
                break
        return count
    except Exception as e:
        logger.error(f"cache_clear failed: {e}")
        return 0


async def cache_stats() -> dict:
    """Return cache statistics."""
    try:
        redis = await _get_redis()
        count = 0
        cursor = 0
        while True:
            cursor, keys = await redis.scan(cursor, match=f"{_KEY_PREFIX}*", count=100)
            count += len(keys)
            if cursor == 0:
                break
        info = await redis.info("memory")
        return {
            "size": count,
            "max_size": -1,  # Redis manages its own memory
            "utilization": 0,
            "redis_used_memory": info.get("used_memory_human", "N/A"),
        }
    except Exception as e:
        logger.error(f"cache_stats failed: {e}")
        return {"size": 0, "max_size": -1, "utilization": 0, "error": str(e)}
