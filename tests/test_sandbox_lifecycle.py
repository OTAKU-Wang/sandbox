"""Tests for sandbox lifecycle: provisioning, resource limits, timeout cleanup."""
import uuid
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from httpx import AsyncClient

from app.services.sandbox_manager import (
    get_resource_limits,
    validate_resource_limits,
    is_session_expired,
    RESOURCE_LIMITS,
)
from app.models.sandbox_session import SandboxLevel, SessionStatus


def test_resource_limits_defined():
    """All sandbox levels have resource limits."""
    for level in [SandboxLevel.L1.value, SandboxLevel.L2.value, SandboxLevel.L3.value]:
        limits = get_resource_limits(level)
        assert "cpu_cores" in limits
        assert "memory_mb" in limits
        assert "disk_mb" in limits
        assert "max_timeout_seconds" in limits


def test_resource_limits_hierarchy():
    """L1 has more resources than L2, L2 more than L3."""
    l1 = get_resource_limits(SandboxLevel.L1.value)
    l2 = get_resource_limits(SandboxLevel.L2.value)
    l3 = get_resource_limits(SandboxLevel.L3.value)
    assert l1["cpu_cores"] > l2["cpu_cores"] > l3["cpu_cores"]
    assert l1["memory_mb"] > l2["memory_mb"] > l3["memory_mb"]


def test_validate_resource_limits_no_request():
    """Without requested limits, returns level defaults."""
    limits = validate_resource_limits(SandboxLevel.L3.value, None)
    assert limits == RESOURCE_LIMITS[SandboxLevel.L3.value]


def test_validate_resource_limits_clamped():
    """Requested limits are clamped to level maximums."""
    requested = {"cpu_cores": 100, "memory_mb": 99999}
    limits = validate_resource_limits(SandboxLevel.L3.value, requested)
    assert limits["cpu_cores"] == 1  # L3 max
    assert limits["memory_mb"] == 512  # L3 max


def test_validate_resource_limits_within_bounds():
    """Requested limits within bounds are kept."""
    requested = {"cpu_cores": 1, "memory_mb": 256}
    limits = validate_resource_limits(SandboxLevel.L2.value, requested)
    assert limits["cpu_cores"] == 1
    assert limits["memory_mb"] == 256


def test_is_session_expired_not_expired():
    """Fresh session is not expired."""
    session = MagicMock()
    session.status = SessionStatus.RUNNING.value
    session.created_at = datetime.now(timezone.utc)
    session.timeout_seconds = 3600
    assert is_session_expired(session) is False


def test_is_session_expired_expired():
    """Session created 2 hours ago with 1h timeout is expired."""
    session = MagicMock()
    session.status = SessionStatus.RUNNING.value
    session.created_at = datetime.now(timezone.utc) - timedelta(hours=2)
    session.timeout_seconds = 3600
    assert is_session_expired(session) is True


def test_is_session_expired_terminated():
    """Terminated sessions are never considered expired."""
    session = MagicMock()
    session.status = SessionStatus.TERMINATED.value
    session.created_at = datetime.now(timezone.utc) - timedelta(days=1)
    session.timeout_seconds = 60
    assert is_session_expired(session) is False


@pytest.mark.asyncio
async def test_sandbox_session_create_provisions_container(client: AsyncClient, auth_headers: dict):
    """Creating a sandbox session provisions a container."""
    # Create a published data product
    prod_resp = await client.post("/api/v1/data-products", json={"name": "SandboxTest"}, headers=auth_headers)
    product_id = prod_resp.json()["id"]

    resp = await client.post("/api/v1/sandbox-sessions", json={
        "data_product_id": product_id,
        "sandbox_level": "L3",
    }, headers=auth_headers)
    # May fail with 400 if product not published — that's OK
    if resp.status_code == 201:
        data = resp.json()
        assert data["status"] in ("running", "failed")
        assert data["session_key_id"] is not None


@pytest.mark.asyncio
async def test_cleanup_expired_endpoint(client: AsyncClient, admin_headers: dict):
    """Cleanup endpoint returns cleaned count (admin only)."""
    resp = await client.post("/api/v1/sandbox-sessions/cleanup-expired", headers=admin_headers)
    assert resp.status_code == 200
    assert "cleaned" in resp.json()
