"""Shared async Redis client — singleton with connection pooling."""
import re
import time
import logging
import redis.asyncio as aioredis

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_redis_client: aioredis.Redis | None = None
_last_connect_failure: float = 0.0
_CONNECT_COOLDOWN = 30.0  # seconds between reconnection attempts

# A38: REDIS_URL 在生产形态就是 redis://:<password>@host:6379/0，而日志在
# CDS_LOG_JSON=true 下直接进采集链路 —— 凭据一个字都不许落进日志。
_CRED_IN_URL = re.compile(r"([a-z][a-z0-9+.-]*://)[^/@\s]*@", re.IGNORECASE)
_SECRET_QUERY = re.compile(r"([?&;](?:password|passwd|auth|token)=)[^&;\s]*", re.IGNORECASE)


def redact_url(text: str) -> str:
    """抹掉 URL 形态文本里的凭据，保留 host:port/db 供排障（A38）。

    也用于异常文本：redis 驱动的报错会把整条连接串回显出来。
    """
    if not text:
        return text
    redacted = _CRED_IN_URL.sub(r"\1***@", text)
    return _SECRET_QUERY.sub(r"\1***", redacted)


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
        # 惰性格式化 + 去凭据：绝不把 REDIS_URL 原文写进日志
        logger.info("Redis connected: %s", redact_url(settings.REDIS_URL))
    except Exception as e:
        logger.error("Redis connection failed: %s", redact_url(str(e)))
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
