"""A31（high）— 「没有治理合约」被当成「合约已失效」，无合约会话一旦暂停即死锁。

台账: docs/ocr-review-2026-10-18/ocr-verified-findings.md §A31

台账点名的四个隐式调用点里，`tests/test_idle_autopause.py` 只覆盖了
`POST /{id}/refreshes`。本文件把剩下的调用点补上钉：文件面（经
`_load_operable_session`）与手工 `POST /{id}/resume`，并在两条路上保住
fail-closed 边界（挂着 contract_id 而行不存在 ⇒ 仍 410）。
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.models.sandbox_session import SandboxSession


class _DummyRuntime:
    def get_adapter(self, level: str):
        return self

    def provision(self, **kwargs):
        return {"container_id": "dummy-a31", "status": "running"}

    async def execute(self, container_id: str, code: str, language: str, **kwargs):
        return {"output": "", "exit_code": 0}

    def terminate(self, container_id: str) -> bool:
        return True


async def _suspended_contractless_session(
    client: AsyncClient,
    auth_headers: dict,
    publish_product,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    *,
    contract_id: object | None = None,
) -> str:
    import app.api.sandbox_sessions as api

    runtime = _DummyRuntime()

    async def fake_get_sandbox_manager():
        return runtime

    monkeypatch.setattr(api, "get_sandbox_manager", fake_get_sandbox_manager)

    product_resp = await client.post(
        "/api/v1/data-products", json={"name": name}, headers=auth_headers
    )
    product_id = product_resp.json()["id"]
    await publish_product(product_id)
    session_resp = await client.post(
        "/api/v1/sandbox-sessions", json={"data_product_id": product_id}, headers=auth_headers
    )
    assert session_resp.status_code == 201, session_resp.text
    session_id = session_resp.json()["id"]

    row = await db_session.get(SandboxSession, uuid.UUID(session_id))
    row.status = "suspended"
    row.pre_pause_status = "running"
    row.auto_resume = True
    row.contract_id = contract_id
    await db_session.flush()
    return session_id


async def test_file_surface_wakes_contractless_suspended_session(
    client: AsyncClient,
    auth_headers: dict,
    publish_product,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A31: 文件面取数不得把「无合约」读成「授权已撤销」。"""
    from app.services.sandbox_runtime import sandbox_runtime

    monkeypatch.setattr(sandbox_runtime, "get_workspace", lambda cid, level: tmp_path)
    session_id = await _suspended_contractless_session(
        client, auth_headers, publish_product, db_session, monkeypatch, f"A31F-{uuid.uuid4().hex[:6]}"
    )

    resp = await client.get(f"/api/v1/sandbox-sessions/{session_id}/files", headers=auth_headers)

    assert resp.status_code != 410, f"A31: no-contract session was treated as revoked: {resp.text[:200]}"
    assert resp.status_code == 200, resp.text

    after = await db_session.get(SandboxSession, uuid.UUID(session_id))
    await db_session.refresh(after)
    assert after.status == "running", after.status


async def test_manual_resume_route_is_not_dead_for_contractless_session(
    client: AsyncClient,
    auth_headers: dict,
    publish_product,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A31: 报错文案建议的手工 resume 不得对无合约会话回 410。"""
    session_id = await _suspended_contractless_session(
        client, auth_headers, publish_product, db_session, monkeypatch, f"A31R-{uuid.uuid4().hex[:6]}"
    )

    resp = await client.post(f"/api/v1/sandbox-sessions/{session_id}/resume", headers=auth_headers)

    assert resp.status_code != 410, f"A31: manual resume refused a contractless session: {resp.text[:200]}"

    after = await db_session.get(SandboxSession, uuid.UUID(session_id))
    await db_session.refresh(after)
    assert after.status == "running", after.status


async def test_dangling_contract_id_still_refuses_wake_on_file_surface(
    client: AsyncClient,
    auth_headers: dict,
    publish_product,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A31 的 fail-closed 边界（文件面）：挂着不存在的 contract_id 仍须 410。"""
    from app.services.sandbox_runtime import sandbox_runtime

    monkeypatch.setattr(sandbox_runtime, "get_workspace", lambda cid, level: tmp_path)
    session_id = await _suspended_contractless_session(
        client, auth_headers, publish_product, db_session, monkeypatch,
        f"A31D-{uuid.uuid4().hex[:6]}", contract_id=uuid.uuid4(),
    )

    resp = await client.get(f"/api/v1/sandbox-sessions/{session_id}/files", headers=auth_headers)

    assert resp.status_code == 410, resp.text
    after = await db_session.get(SandboxSession, uuid.UUID(session_id))
    await db_session.refresh(after)
    assert after.status == "suspended", after.status
