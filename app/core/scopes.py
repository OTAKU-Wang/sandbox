"""SS-12 — platform API key scope domains (machine-credential authorization).

Functional-permission model for `cds_ak_` keys (spec ss-12-agent-api-keys.md §4.2):

- scope grammar: `<group>:<verb>`, verb ∈ {read, write, *}; `*` = read+write of the group.
- group ← request-path prefix map is LONGEST-PREFIX-FIRST; an unmapped prefix
  is DEFAULT-DENIED for key subjects (fail-closed, same discipline as the
  OPA default-deny anchor) while user subjects pass untouched.
- human-only groups (auth / users / api-keys / internal / static) have NO
  scope at all: key subjects are always rejected there (`require_human`).
- brute-force cooldown: 5 consecutive auth failures per key_hash → 15 min
  cooldown. IN-PROCESS counters (honestly approximate under multi-instance
  deployments — disclosed in docs, never claimed global).
- per-key rate limiting: fixed per-minute window mirroring the quota_manager
  pattern (Redis when wired, in-process fallback).
"""
import time
from collections import OrderedDict
from typing import Callable

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.errors import CDSError

# --- scope grammar -----------------------------------------------------------

VERBS = ("read", "write", "*")

# Group → covered route prefixes, per spec 4.2 (mounted in app/main.py).
# Order is irrelevant — matching is longest-prefix-first at runtime.
GROUP_PREFIXES: list[tuple[str, tuple[str, ...]]] = [
    ("sessions", ("/api/v1/sandbox-sessions", "/api/v1/dev-sandbox")),
    ("contracts", ("/api/v1/contracts",)),
    ("data", ("/api/v1/data-products", "/api/v1/data-resources", "/api/v1/catalog")),
    ("pipeline", ("/api/v1/data-pipeline", "/api/v1/training", "/api/v1/mpc",
                  "/api/v1/rag", "/api/v1/inference", "/api/v1/sandbox-db",
                  "/api/v1/sandbox-tasks")),
    ("audit", ("/api/v1/audit", "/api/v1/monitoring", "/api/v1/compliance",
               "/metrics", "/health")),
    ("output", ("/api/v1/output-control", "/api/v1/field-exposure",
                "/api/v1/network-policies")),
    ("infra", ("/api/v1/sandbox-nodes", "/api/v1/shared-volumes",
               "/api/v1/cube-templates", "/api/v1/kms", "/api/v1/connectors",
               "/api/v1/ops")),
    ("certs", ("/api/v1/certificates",)),
    ("federation", ("/api/v1/federation",)),
    ("gateway", ("/api/v1/gateway",)),
    ("agent", ("/api/v1/agent",)),
    ("meta", ("/api/v1/meta",)),
]

REACHABLE_GROUPS = [g for g, _ in GROUP_PREFIXES]

# No scope exists for these: key subjects are ALWAYS rejected (spec 4.2).
HUMAN_ONLY_PREFIXES = ("/api/v1/auth", "/api/v1/users", "/api/v1/api-keys",
                       "/internal", "/static")


def resolve_group(path: str) -> str | None:
    """Map a request path to its functional group (longest prefix first).

    Returns None when the path is not covered by any group — the default-deny
    signal for key subjects (user subjects are unaffected).
    """
    if is_human_only(path):
        return None
    best: tuple[int, str] | None = None
    for group, prefixes in GROUP_PREFIXES:
        for prefix in prefixes:
            if path == prefix or path.startswith(prefix + "/") or path.startswith(prefix):
                if best is None or len(prefix) > best[0]:
                    best = (len(prefix), group)
    return best[1] if best else None


def is_human_only(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") or path.startswith(p)
               for p in HUMAN_ONLY_PREFIXES)


def method_verb(method: str) -> str:
    """GET → read; every other method → write (spec 4.2 rule 2)."""
    return "read" if method.upper() == "GET" else "write"


def check_scope(scopes: list[str] | None, group: str, verb: str) -> bool:
    """Key passes iff its scopes contain `group:verb` or `group:*`."""
    if not scopes:
        return False
    for scope in scopes:
        if scope == f"{group}:*" or scope == f"{group}:{verb}":
            return True
    return False


def expand_preset(preset: str, scopes: list[str] | None = None) -> list[str]:
    """Expand a creation-time preset into the explicit scope list (spec 4.2).

    Presets are expanded at create time and stored verbatim — later preset
    changes never affect existing keys. `custom` requires a valid scope list.
    """
    if preset == "readonly":
        return [f"{g}:read" for g in REACHABLE_GROUPS]
    if preset == "operator":
        write_groups = ("sessions", "contracts", "data", "pipeline",
                        "output", "gateway", "agent")
        return [f"{g}:read" for g in REACHABLE_GROUPS] + [f"{g}:write" for g in write_groups]
    if preset == "full":
        return [f"{g}:*" for g in REACHABLE_GROUPS]
    if preset == "custom":
        if not scopes:
            raise ValueError("preset=custom requires a non-empty scopes list")
        for scope in scopes:
            validate_scope(scope)
        return list(scopes)
    raise ValueError(f"unknown preset: {preset}")


def validate_scope(scope: str) -> None:
    """Validate one scope string `<group>:<verb>`; raise ValueError otherwise."""
    parts = scope.split(":")
    if len(parts) != 2 or parts[0] not in REACHABLE_GROUPS or parts[1] not in VERBS:
        raise ValueError(f"invalid scope: {scope!r}")


# --- brute-force cooldown (in-process, honestly approximate) -----------------

COOLDOWN_FAILURE_THRESHOLD = 5
COOLDOWN_SECONDS = 15 * 60
# Bound for the in-process auth-failure/cooldown tables. Without it, every
# wrong-key guess from the internet adds a permanent entry (unbounded memory
# growth driven by unauthenticated traffic).
MAX_TRACKED_AUTH_KEYS = 10_000

# key_hash → (consecutive failures, last-seen monotonic clock), recency-ordered
_auth_failures: "OrderedDict[str, tuple[int, float]]" = OrderedDict()
# key_hash → cooldown end (monotonic clock), insertion-ordered (earliest end first)
_cooldown_until: dict[str, float] = {}


def _prune_front(table: dict, cutoff: float, limit: int) -> None:
    """Drop entries whose timestamp is at or before ``cutoff`` from the oldest
    end, then enforce the size bound.

    Amortized O(1): both tables are ordered so the front is the entry allowed
    to expire first. Callers pick the cutoff — a failure counter goes stale one
    cooldown window after its last failure, a cooldown entry once its deadline
    passed.
    """
    while table:
        oldest = next(iter(table))
        value = table[oldest]
        seen = value[1] if isinstance(value, tuple) else value
        if seen > cutoff:
            break
        table.pop(oldest)
    while len(table) > limit:
        table.pop(next(iter(table)))


def reset_auth_counters() -> None:
    """Test helper — clear all in-process cooldown state."""
    _auth_failures.clear()
    _cooldown_until.clear()


def record_auth_failure(key_hash: str) -> None:
    """Count one auth failure; N consecutive failures arm the cooldown."""
    now = time.monotonic()
    count = _auth_failures.get(key_hash, (0, now))[0] + 1
    _auth_failures[key_hash] = (count, now)
    _auth_failures.move_to_end(key_hash)
    if count >= COOLDOWN_FAILURE_THRESHOLD:
        _cooldown_until[key_hash] = now + COOLDOWN_SECONDS
    # 上限在写入之后统一收口：先裁剪后写入会让两张表最大到 limit + 1。
    _prune_front(_auth_failures, now - COOLDOWN_SECONDS, MAX_TRACKED_AUTH_KEYS)
    _prune_front(_cooldown_until, now, MAX_TRACKED_AUTH_KEYS)


def clear_auth_failures(key_hash: str) -> None:
    _auth_failures.pop(key_hash, None)
    _cooldown_until.pop(key_hash, None)


def cooldown_remaining_seconds(key_hash: str) -> float:
    """Seconds left in the cooldown window (0 = not cooling down)."""
    end = _cooldown_until.get(key_hash)
    if end is None:
        return 0.0
    remaining = end - time.monotonic()
    if remaining <= 0:
        _cooldown_until.pop(key_hash, None)
        return 0.0
    return remaining


# --- per-key rate limiting (fixed per-minute window, quota_manager pattern) --

_key_rate_limits: dict[str, tuple[int, int]] = {}  # key_id → (minute, count)


def reset_rate_limits() -> None:
    """Test helper — clear all in-process per-key rate-limit state."""
    _key_rate_limits.clear()


async def check_key_rate_limit(key_id: str, limit: int) -> tuple[bool, int]:
    """Allow/deny one request against the key's per-minute limit.

    Returns (allowed, retry_after_seconds). Redis fixed window when the
    quota_manager Redis client is wired, in-process fallback otherwise —
    the same fail-open/availability posture as quota_manager.
    """
    now = time.time()
    minute = int(now // 60)
    retry_after = max(1, int(60 - (now % 60)))

    from app.services.quota_manager import quota_manager
    redis = getattr(quota_manager, "_redis", None)
    if redis is not None:
        try:
            rkey = f"apik:{key_id}:{minute}"
            count = await redis.incr(rkey)
            if int(count) == 1:
                await redis.expire(rkey, 60)
            return (int(count) <= limit), retry_after
        except Exception:
            pass  # local fallback below

    window, count = _key_rate_limits.get(key_id, (minute, 0))
    if window != minute:
        window, count = minute, 0
    if count >= limit:
        return False, retry_after
    _key_rate_limits[key_id] = (window, count + 1)
    return True, retry_after


# --- auth gates ---------------------------------------------------------------

# auto_error=False so a missing Authorization header reaches the route's own
# auth dependency (and its 401) instead of HTTPBearer's default 403.
subject_security = HTTPBearer(auto_error=False)


async def _optional_subject(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(subject_security),
    db: AsyncSession = Depends(get_db),
):
    """Resolve the auth subject when Bearer credentials are present.

    Returns None for anonymous requests — these gates only police MACHINE
    subjects; anonymous access is the responsibility of the route's own
    auth dependency (byte-preserved behavior for the human/JWT path).
    The resolved subject is cached on request.state so a single request
    never authenticates (and never double-counts rate limits / audits) more
    than once even when both the group gate and the route auth run.
    """
    if credentials is None or not credentials.credentials:
        return None
    cached = getattr(request.state, "_ss12_subject", None)
    if cached is not None:
        return cached
    from fastapi import HTTPException
    from app.core.deps import resolve_auth_subject  # lazy: deps imports this module
    try:
        subject = await resolve_auth_subject(credentials.credentials, db, request)
    except HTTPException:
        # Non-subject machine credentials (KMS service token, connector key,
        # cube audit token, metrics token) sit outside the unified-subject
        # model: pass through and let the route's own fail-closed token
        # dependency decide. CDSError (key-path denials, audited) still
        # propagates.
        return None
    request.state._ss12_subject = subject
    return subject


def require_group(group: str) -> Callable:
    """Router-level scope gate for one functional group (spec 4.2 rule 3).

    User subjects: no-op (their existing require_roles role gates apply).
    Key subjects: need `<group>:<verb>` or `<group>:*`, else 403
    API_KEY_SCOPE_DENIED.
    """
    async def checker(
        request: Request,
        subject = Depends(_optional_subject),
    ) -> None:
        if subject is None or subject.kind != "api_key":
            return
        verb = method_verb(request.method)
        if not check_scope(subject.api_key.scopes, group, verb):
            raise CDSError(
                f"API key lacks scope {group}:{verb}",
                code="API_KEY_SCOPE_DENIED",
                http_status=403,
                detail={"group": group, "verb": verb, "path": request.url.path},
            )
    return checker


async def require_human(
    request: Request,
    subject = Depends(_optional_subject),
) -> None:
    """Human-only gate (auth / users / api-keys / internal / static).

    No scope exists for these surfaces, so a key subject is always rejected
    with API_KEY_SCOPE_DENIED (spec 4.3 — no new code).
    """
    if subject is not None and subject.kind == "api_key":
        raise CDSError(
            "API key subjects cannot access human-only endpoints",
            code="API_KEY_SCOPE_DENIED",
            http_status=403,
            detail={"path": request.url.path, "reason": "human_only"},
        )


def key_unmapped_deny(path: str, auth_header: str) -> bool:
    """SS-12 rule 1 predicate: True when a `cds_ak_` key subject hits a path
    no functional group covers (and which is not human-only).

    Pure function so the default-deny check runs in raw ASGI middleware
    (WebSocket handshakes cannot inject Request-taking dependencies).
    """
    if not auth_header.startswith("Bearer cds_ak_"):
        return False
    if is_human_only(path):
        return False
    return resolve_group(path) is None
