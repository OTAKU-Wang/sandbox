"""A37（medium-high）— 会话文件上传接口自身没有任何单文件字节上限。

台账: docs/ocr-review-2026-10-18/ocr-verified-findings.md §A37

`upload_session_file` 直接 `await file.read()` 把整个上传体读进 API 进程内存，
本文件里没有任何上限：l2c 分支要等整份字节进内存后才在 exec 通道处被拒（256KB），
工作空间分支的 `SESSION_FILE_MAX_BYTES` 由别处把关，接口自己既不估长度也不封顶。

钉的是接口自身的封顶语义：超限的请求必须在**进入写入通道之前**被拒，且不得留下文件。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.api import sandbox_sessions as ss


@pytest_asyncio.fixture
async def session_row(db_session):
    from app.models.data_product import DataProduct
    from app.models.sandbox_session import SandboxLevel, SandboxMode, SandboxSession
    from app.models.user import User
    from app.services.auth_service import hash_password

    user = User(username=f"up_{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@e2e.local",
                hashed_password=hash_password("testpass123"), role="data_provider")
    db_session.add(user)
    await db_session.flush()
    product = DataProduct(name="UploadCap", provider_id=user.id, product_type="structured")
    db_session.add(product)
    await db_session.flush()
    sid = uuid.uuid4()
    session = SandboxSession(
        id=sid,
        user_id=user.id,
        data_product_id=product.id,
        sandbox_level=SandboxLevel.L3.value,
        sandbox_mode=SandboxMode.STRUCTURED_QUERY.value,
        status="running",
        container_id=f"bwrap-{sid}",
        timeout_seconds=3600,
    )
    db_session.add(session)
    await db_session.flush()
    return session


@pytest_asyncio.fixture
async def owner_headers(db_session, session_row):
    from app.services.auth_service import create_access_token

    return {"Authorization": f"Bearer {create_access_token(session_row.user_id, 'data_provider')}"}


@pytest.fixture
def workspace_client(client, monkeypatch, tmp_path):
    from app.services.sandbox_runtime import sandbox_runtime

    ws = tmp_path / "upload-ws"
    ws.mkdir()
    monkeypatch.setattr(sandbox_runtime, "get_workspace", lambda cid, level: ws)
    return client, ws


async def test_l2c_upload_is_capped_before_the_exec_channel(
    client: AsyncClient, owner_headers: dict, session_row, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A37: l2c 上传超通道上限时，接口必须自己拒掉，而不是整份读进内存再交给 exec。"""
    from app.models.sandbox_session import SandboxLevel

    session_row.sandbox_level = SandboxLevel.L2C.value
    session_row.container_id = f"l2c-node1-{session_row.id.hex}"

    seen: list[bytes] = []

    async def _spy(*args, **kwargs):
        seen.append(kwargs.get("data") or (args[3] if len(args) > 3 else b""))
        return {}

    monkeypatch.setattr(ss, "_l2c_file_op", _spy)

    resp = await client.post(
        f"/api/v1/sandbox-sessions/{session_row.id}/files",
        files={"file": ("big.bin", b"z" * (400 * 1024), "application/octet-stream")},
        headers=owner_headers,
    )

    assert resp.status_code == 413, f"expected 413, got {resp.status_code}: {resp.text[:200]}"
    assert seen == [], "A37: the oversized body was handed to the exec channel before being refused"


async def test_workspace_upload_respects_the_endpoint_ceiling(
    workspace_client, owner_headers: dict, session_row, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A37: 接口自身必须有单文件字节上限；超限不得写进会话工作空间。"""
    client, ws = workspace_client
    monkeypatch.setattr(ss, "MAX_SESSION_UPLOAD_BYTES", 4096, raising=False)

    resp = await client.post(
        f"/api/v1/sandbox-sessions/{session_row.id}/files",
        files={"file": ("big.bin", b"z" * 5000, "application/octet-stream")},
        headers=owner_headers,
    )

    assert resp.status_code == 413, f"expected 413, got {resp.status_code}: {resp.text[:200]}"
    assert not (ws / "files" / "big.bin").exists(), "A37: oversized upload was persisted anyway"
    assert resp.json()["detail"]["limit_bytes"] == 4096


async def test_upload_within_the_ceiling_still_works(
    workspace_client, owner_headers: dict, session_row, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A37 的修复不得收紧到影响正常上传。"""
    client, _ws = workspace_client
    monkeypatch.setattr(ss, "MAX_SESSION_UPLOAD_BYTES", 4096, raising=False)

    resp = await client.post(
        f"/api/v1/sandbox-sessions/{session_row.id}/files",
        files={"file": ("ok.bin", b"z" * 2048, "application/octet-stream")},
        headers=owner_headers,
    )

    assert resp.status_code == 200, resp.text
