"""A36（medium-high）— 网络策略更新先拆后建且无补偿。

台账: docs/ocr-review-2026-10-18/ocr-verified-findings.md §A36

`update_network_policy` 无条件先 `remove_policy`（删 netns/iptables/DKMS 授权），随后
`create_policy` 一抛就 500：库里的旧策略还在（事务回滚），运行面却已是一片空白，
而且没有任何审计写过这件事。要求：失败必须把旧策略装回去（补偿），并把 500 收成
显式 5xx + 如实审计。
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.api.sandbox_sessions import network_policy_engine
from app.models.audit_log import AuditLog


class _DummyRuntime:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_adapter(self, level: str):
        return self

    def provision(self, **kwargs):
        return {"container_id": "dummy-a36", "status": "running"}

    async def execute(self, container_id: str, code: str, language: str, **kwargs):
        return {"output": "", "exit_code": 0}

    def terminate(self, container_id: str) -> bool:
        self.calls.append(f"terminate:{container_id}")
        return True


def _patch_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.api.sandbox_sessions as api

    runtime = _DummyRuntime()

    async def fake_get_sandbox_manager():
        return runtime

    monkeypatch.setattr(api, "get_sandbox_manager", fake_get_sandbox_manager)


def _boom_on_allowlist(monkeypatch: pytest.MonkeyPatch, engine_calls: list) -> None:
    async def fake_remove_policy(session_id: str, db=None):
        engine_calls.append(("remove", session_id))

    async def fake_create_policy(session_id: str, config, db=None):
        engine_calls.append(("create", config.mode))
        if config.mode == "allowlist":
            raise RuntimeError("iptables boom")
        return {"session_id": session_id, "mode": config.mode}

    monkeypatch.setattr(network_policy_engine, "remove_policy", fake_remove_policy)
    monkeypatch.setattr(network_policy_engine, "create_policy", fake_create_policy)


async def _running_session(
    client: AsyncClient, auth_headers: dict, publish_product, name: str
) -> str:
    product_resp = await client.post(
        "/api/v1/data-products", json={"name": name}, headers=auth_headers
    )
    product_id = product_resp.json()["id"]
    await publish_product(product_id)
    session_resp = await client.post(
        "/api/v1/sandbox-sessions",
        json={"data_product_id": product_id, "network_policy": {"mode": "deny_all", "active": True}},
        headers=auth_headers,
    )
    assert session_resp.status_code == 201, session_resp.text
    return session_resp.json()["id"]


async def test_failed_policy_replacement_restores_previous_enforcement(
    client: AsyncClient,
    auth_headers: dict,
    monkeypatch: pytest.MonkeyPatch,
    publish_product,
) -> None:
    """A36: 新策略建不起来时，旧策略必须回到运行面上。"""
    engine_calls: list = []
    _patch_runtime(monkeypatch)
    _boom_on_allowlist(monkeypatch, engine_calls)

    session_id = await _running_session(client, auth_headers, publish_product, f"A36-{uuid.uuid4().hex[:6]}")

    resp = await client.put(
        f"/api/v1/sandbox-sessions/{session_id}/network-policy",
        json={"mode": "allowlist", "allowed_ips": ["10.0.0.0/8"], "active": True},
        headers=auth_headers,
    )

    assert resp.status_code == 502, f"expected explicit 502, got {resp.status_code}: {resp.text[:300]}"

    # 注：engine_calls 是从**建会话**那一次 create 开始记录的（打桩早于 _running_session），
    # 所以对整表做 `.index(("create", "deny_all"))` 取到的是开通时的 deny_all，而不是补偿
    # 那一次——原判据因此永远为假。这里改钉真正要证的事：失败之后引擎听到的**最后一句**
    # 就是把旧策略装回去，且它排在失败的 create 之后。
    modes = [c for c in engine_calls if c[0] == "create"]
    assert ("create", "allowlist") in modes, engine_calls
    assert modes[-1] == ("create", "deny_all"), (
        f"A36: previous policy was never restored, engine left with nothing: {engine_calls}"
    )
    assert modes.index(("create", "allowlist")) < len(modes) - 1, engine_calls

    still_old = await client.get(
        f"/api/v1/sandbox-sessions/{session_id}/network-policy", headers=auth_headers
    )
    assert still_old.status_code == 200
    assert still_old.json()["mode"] == "deny_all", "控制面不得留下半截新策略"


async def test_policy_replacement_failure_is_audited(
    client: AsyncClient,
    auth_headers: dict,
    monkeypatch: pytest.MonkeyPatch,
    publish_product,
    db_session,
) -> None:
    """A36: 这次「运行面空白」必须留下审计，并如实写明旧策略是否已回滚。"""
    engine_calls: list = []
    _patch_runtime(monkeypatch)
    _boom_on_allowlist(monkeypatch, engine_calls)

    session_id = await _running_session(client, auth_headers, publish_product, f"A36B-{uuid.uuid4().hex[:6]}")

    resp = await client.put(
        f"/api/v1/sandbox-sessions/{session_id}/network-policy",
        json={"mode": "allowlist", "allowed_ips": ["10.0.0.0/8"], "active": True},
        headers=auth_headers,
    )
    assert resp.status_code == 502

    rows = (
        (
            await db_session.execute(
                select(AuditLog)
                .where(AuditLog.session_id == uuid.UUID(str(session_id)))
                .where(AuditLog.action == "sandbox.update_network_policy_failed")
            )
        )
        .scalars()
        .all()
    )
    assert rows, "A36: no audit record for a failed network-policy replacement"
    detail = rows[-1].detail or {}
    assert detail.get("previous_restored") is True, detail
    assert detail.get("error"), detail
