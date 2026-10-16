"""Single-use consumption store for server-issued auth challenges (B1 fix).

`/auth/login/cert` signs a *server-issued* challenge; this module guarantees
that a challenge can be spent at most once. Redis is the shared source of
truth (first worker to SET the key wins); when Redis is unreachable the store
degrades to a bounded in-process set and says so LOUDLY, because per-replica
single-use means a captured request body could still be replayed against a
different worker until the challenge expires. Disclosed degradation, never
silent — same posture as `core.cache` / the fail-open rate limiter.
"""
import hashlib
import logging
import time
from collections import OrderedDict

logger = logging.getLogger(__name__)

# Matches auth_service.CERT_CHALLENGE_TTL_SECONDS; a spent challenge only needs
# to stay remembered until the token itself can no longer validate.
CONSUMED_TTL_SECONDS = 300
_MAX_LOCAL_ENTRIES = 4096
_WARNING_INTERVAL_SECONDS = 30.0

_consumed: "OrderedDict[str, float]" = OrderedDict()
_last_warning = 0.0


def _token_id(token: str) -> str:
    """Hash the challenge so raw bearer-ish material never lands in Redis keys."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _prune_local(now: float) -> None:
    for stale in [key for key, expires_at in _consumed.items() if expires_at <= now]:
        _consumed.pop(stale, None)
    while len(_consumed) > _MAX_LOCAL_ENTRIES:
        _consumed.popitem(last=False)


async def consume(token: str) -> bool:
    """Spend a challenge. True = this call is its first and only use."""
    global _last_warning

    now = time.time()
    token_id = _token_id(token)
    try:
        from app.core.redis import get_redis

        redis = await get_redis()
        # SETNX + TTL is the atomic "first setter wins"; no read-then-write race.
        return bool(await redis.set(f"cds:challenge:consumed:{token_id}", "1", nx=True,
                                    ex=CONSUMED_TTL_SECONDS))
    except Exception as exc:
        if now - _last_warning > _WARNING_INTERVAL_SECONDS:
            _last_warning = now
            logger.warning(
                "[CHALLENGE] Redis unavailable — single-use challenge tracking degrades to this "
                "process only; a captured cert-login request can be replayed against another "
                "worker until the challenge expires (%ds): %s",
                CONSUMED_TTL_SECONDS, exc,
            )
        _prune_local(now)
        if token_id in _consumed:
            return False
        _consumed[token_id] = now + CONSUMED_TTL_SECONDS
        return True


def reset() -> None:
    """Test helper — clear the in-process fallback state."""
    _consumed.clear()
