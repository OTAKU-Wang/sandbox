"""A294（high）— 输出审查的脱敏披露只有响应头一个载体，读不到就静默当成"已通过"。

台账: docs/ocr-review-2026-10-18/ocr-verified-findings.md §A294

后端这一半（CORS `expose_headers` 放行 `X-CDS-Output-Review` 与 `Content-Disposition`）
已在 HEAD 修好并由 tests/test_cors_expose_headers.py 钉住；剩下的第三态在前端
（`review === null` 现在什么都不渲染），不在本轨道的文件清单内。

本文件钉住本文件负责的后端契约：两条下载通道都必须把"是否脱敏"如实写进响应头，
脱敏时正文确实被改写、审计另记一笔，未命中时正文逐字节不动且声明 passed；
二进制不可脱敏时不得谎称已脱敏，但必须把命中条数摊开。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.models.audit_log import AuditLog

EMAIL_ROW = b"id,mail\n1,alice@example.com\n"
CLEAN_ROW = b"id,mail\n1,alice\n"
BINARY = b"\x00\x01\x02\x03\xff\xfe" + EMAIL_ROW


@pytest_asyncio.fixture
async def session_row(db_session):
    from app.models.data_product import DataProduct
    from app.models.sandbox_session import SandboxLevel, SandboxMode, SandboxSession
    from app.models.user import User
    from app.services.auth_service import hash_password

    user = User(username=f"rv_{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@e2e.local",
                hashed_password=hash_password("testpass123"), role="data_provider")
    db_session.add(user)
    await db_session.flush()
    product = DataProduct(name="ReviewDisclose", provider_id=user.id, product_type="structured")
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
async def reviewer_headers(db_session, session_row):
    from app.services.auth_service import create_access_token

    return {"Authorization": f"Bearer {create_access_token(session_row.user_id, 'data_provider')}"}


@pytest.fixture
def review_client(client, monkeypatch, tmp_path):
    from app.services.sandbox_runtime import sandbox_runtime

    ws = tmp_path / "review-ws"
    ws.mkdir()
    monkeypatch.setattr(sandbox_runtime, "get_workspace", lambda cid, level: ws)
    return client, ws


async def test_redacted_release_is_disclosed(review_client, reviewer_headers, session_row, db_session):
    """A294: 命中且已脱敏 ⇒ 正文改写 + 头声明 redacted + 审计另记一笔。"""
    client, _ws = review_client

    up = await client.post(
        f"/api/v1/sandbox-sessions/{session_row.id}/files",
        files={"file": ("mail.csv", EMAIL_ROW, "text/csv")},
        headers=reviewer_headers,
    )
    assert up.status_code == 200, up.text

    resp = await client.get(
        f"/api/v1/sandbox-sessions/{session_row.id}/files/mail.csv", headers=reviewer_headers
    )
    assert resp.status_code == 200, resp.text
    header = resp.headers.get("X-CDS-Output-Review", "")
    assert header.startswith("redacted"), header
    assert b"alice@example.com" not in resp.content
    assert b"[REDACTED:email]" in resp.content

    rows = (
        (
            await db_session.execute(
                select(AuditLog)
                .where(AuditLog.session_id == uuid.UUID(str(session_row.id)))
                .where(AuditLog.action == "sandbox.file_download_redacted")
            )
        )
        .scalars()
        .all()
    )
    assert rows, "redaction happened but was never audited"


async def test_clean_release_is_byte_identical(review_client, reviewer_headers, session_row):
    """A294: 未命中 ⇒ 正文逐字节不动，头如实声明 passed。"""
    client, _ws = review_client

    up = await client.post(
        f"/api/v1/sandbox-sessions/{session_row.id}/files",
        files={"file": ("clean.csv", CLEAN_ROW, "text/csv")},
        headers=reviewer_headers,
    )
    assert up.status_code == 200, up.text

    resp = await client.get(
        f"/api/v1/sandbox-sessions/{session_row.id}/files/clean.csv", headers=reviewer_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.content == CLEAN_ROW
    assert resp.headers.get("X-CDS-Output-Review", "").startswith("passed")


async def test_binary_release_discloses_findings_without_claiming_redaction(
    review_client, reviewer_headers, session_row
):
    """A294: 二进制不可脱敏 ⇒ 不得谎称 redacted，但必须摊开命中条数。"""
    client, _ws = review_client

    up = await client.post(
        f"/api/v1/sandbox-sessions/{session_row.id}/files",
        files={"file": ("blob.bin", BINARY, "application/octet-stream")},
        headers=reviewer_headers,
    )
    assert up.status_code == 200, up.text

    resp = await client.get(
        f"/api/v1/sandbox-sessions/{session_row.id}/files/blob.bin", headers=reviewer_headers
    )
    assert resp.status_code == 200, resp.text
    header = resp.headers.get("X-CDS-Output-Review", "")
    assert not header.startswith("redacted"), header
    assert "findings=0" not in header, header
