"""Shared async Redis client — singleton with connection pooling."""
import time
import logging
import redis.asyncio as aioredis

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_redis_client: aioredis.Redis | None = None
_last_connect_failure: float = 0.0
_CONNECT_COOLDOWN = 30.0  # seconds between reconnection attempts


async def get_redis() -> aioredis.Redis:
    """Get or create the shared Redis client."""
    global _redis_client, _last_connect_failure

    if _redis_client is not None:
        return _redis_client

    # Don't retry too frequently after a failure
    if _last_connect_failure and (time.monotonic() - _last_connect_failure) < _CONNECT_COOLDOWN:
        raise ConnectionError("Redis unavailable (cooldown)")

    settings = get_settings()
    _redis_client = aioredis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
        max_connections=20,
        socket_connect_timeout=2,
        socket_timeout=2,
        retry_on_timeout=False,
    )
    try:
        await _redis_client.ping()
        _last_connect_failure = 0.0
        logger.info(f"Redis connected: {settings.REDIS_URL}")
    except Exception as e:
        logger.error(f"Redis connection failed: {e}")
        await _redis_client.aclose()
        _redis_client = None
        _last_connect_failure = time.monotonic()
        raise
    return _redis_client


async def close_redis() -> None:
    """Close the Redis client on shutdown."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
        logger.info("Redis connection closed")
