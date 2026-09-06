"""Sandbox Manager — orchestrates sandbox lifecycle with resource enforcement.

Integrates SandboxRuntime with database-backed session management.
Enforces resource limits per sandbox level and handles session timeouts.
Thread-safe via asyncio.Lock for multi-tenant concurrent access.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta

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
    """Get resource limits for a sandbox level."""
    return RESOURCE_LIMITS.get(sandbox_level, RESOURCE_LIMITS[SandboxLevel.L3.value])


def validate_resource_limits(sandbox_level: str, requested_limits: dict | None) -> dict:
    """Validate and clamp requested resource limits to level maximums.

    Returns the effective resource limits dict.
    """
    level_limits = get_resource_limits(sandbox_level)
    if not requested_limits:
        return level_limits

    effective = level_limits.copy()
    if "cpu_cores" in requested_limits:
        effective["cpu_cores"] = min(requested_limits["cpu_cores"], level_limits["cpu_cores"])
    if "memory_mb" in requested_limits:
        effective["memory_mb"] = min(requested_limits["memory_mb"], level_limits["memory_mb"])
    if "disk_mb" in requested_limits:
        effective["disk_mb"] = min(requested_limits["disk_mb"], level_limits["disk_mb"])
    return effective


def is_session_expired(session) -> bool:
    """Check if a session has exceeded its timeout."""
    if session.status in (SessionStatus.COMPLETED.value, SessionStatus.TERMINATED.value, SessionStatus.FAILED.value):
        return False
    if not session.created_at:
        return False

    # SQLite DateTime(timezone=True) round-trips as naive — normalise to UTC
    # so the comparison never fails with a naive/aware mismatch.
    created = session.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    elapsed = datetime.now(timezone.utc) - created
    timeout = timedelta(seconds=session.timeout_seconds)
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
_tenant_quotas: dict[str, dict] = {}
TENANT_DEFAULTS = {
    "max_sessions": 5,
    "max_cpu_cores": 8,
    "max_memory_mb": 8192,
    "max_disk_mb": 20480,
}


def get_tenant_quota(user_id: str) -> dict:
    """Get or create tenant resource quota."""
    if user_id not in _tenant_quotas:
        _tenant_quotas[user_id] = TENANT_DEFAULTS.copy()
    return _tenant_quotas[user_id]


def update_tenant_usage(user_id: str, cpu_cores: int = 0, memory_mb: int = 0, disk_mb: int = 0):
    """Update aggregated resource usage for a tenant."""
    quota = get_tenant_quota(user_id)
    quota["used_cpu_cores"] = quota.get("used_cpu_cores", 0) + cpu_cores
    quota["used_memory_mb"] = quota.get("used_memory_mb", 0) + memory_mb
    quota["used_disk_mb"] = quota.get("used_disk_mb", 0) + disk_mb


def check_tenant_quota(user_id: str, cpu_cores: int, memory_mb: int, disk_mb: int) -> tuple[bool, str]:
    """Check if a tenant has enough quota for a new session.

    Returns (allowed, reason).
    """
    quota = get_tenant_quota(user_id)
    used_cpu = quota.get("used_cpu_cores", 0)
    used_mem = quota.get("used_memory_mb", 0)
    used_disk = quota.get("used_disk_mb", 0)

    if used_cpu + cpu_cores > quota["max_cpu_cores"]:
        return False, f"CPU quota exceeded ({used_cpu}/{quota['max_cpu_cores']} cores)"
    if used_mem + memory_mb > quota["max_memory_mb"]:
        return False, f"Memory quota exceeded ({used_mem}/{quota['max_memory_mb']} MB)"
    if used_disk + disk_mb > quota["max_disk_mb"]:
        return False, f"Disk quota exceeded ({used_disk}/{quota['max_disk_mb']} MB)"
    return True, ""


def release_tenant_usage(user_id: str, cpu_cores: int = 0, memory_mb: int = 0, disk_mb: int = 0):
    """Release aggregated resource usage for a tenant (on session terminate)."""
    quota = get_tenant_quota(user_id)
    quota["used_cpu_cores"] = max(0, quota.get("used_cpu_cores", 0) - cpu_cores)
    quota["used_memory_mb"] = max(0, quota.get("used_memory_mb", 0) - memory_mb)
    quota["used_disk_mb"] = max(0, quota.get("used_disk_mb", 0) - disk_mb)


async def get_sandbox_manager():
    """Lazy-init sandbox manager with thread-safe singleton."""
    global sandbox_manager
    if sandbox_manager is None:
        async with _manager_lock:
            if sandbox_manager is None:  # Double-check after acquiring lock
                from app.services.sandbox_runtime import SandboxRuntime
                sandbox_manager = SandboxRuntime()
    return sandbox_manager
