"""A155（high）— 网络策略引擎"建了策略"却从未被执行面消费，审计仍按已执行落账。

台账: docs/ocr-review-2026-10-18/ocr-verified-findings.md §A155

`create_policy` 会返回 netns_name / iptables_chain / dns_proxy_port 并落
`policy_applied` 审计，但执行面钩子（`get_bwrap_netns_args` / `get_dns_env_vars` /
`check_connection`）在全仓没有任何调用者 ⇒ 域名/IP 白名单在每条路径上都没生效，
真实生效的是运行时的 `--unshare-net`（全禁）。

本文件钉的是会话路径这一侧的契约：`enforcement` 与审计不得把"引擎里记了一笔、
宿主动了 netns/链"说成"运行面在执行"——只有数据面确认过推送
（`enforced_by=cube_dataplane`）才算已执行，否则会话实际得到的就是运行时的
`--unshare-net` 全禁。
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.api.sandbox_sessions import network_policy_engine
from app.models.audit_log import AuditLog


class _DummyRuntime:
    def get_adapter(self, level: str):
        return self

    def provision(self, **kwargs):
        return {"container_id": "dummy-a155", "status": "running"}

    async def execute(self, container_id: str, code: str, language: str, **kwargs):
        return {"output": "", "exit_code": 0}

    def terminate(self, container_id: str) -> bool:
        return True


def _fake_engine(monkeypatch: pytest.MonkeyPatch, result_factory) -> None:
    async def fake_remove_policy(session_id: str, db=None):
        return None

    monkeypatch.setattr(network_policy_engine, "remove_policy", fake_remove_policy)
    monkeypatch.setattr(network_policy_engine, "create_policy", result_factory)


async def _running_session(client, auth_headers, publish_product, monkeypatch, name: str) -> str:
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
    return session_resp.json()["id"]


async def test_allowlist_update_discloses_that_nothing_enforces_it(
    client: AsyncClient,
    auth_headers: dict,
    monkeypatch: pytest.MonkeyPatch,
    publish_product,
    db_session,
) -> None:
    """A155: 宿主 netns/链名不等于执行——响应与审计都必须如实披露。"""
    async def host_side_policy(session_id: str, config, db=None):
        return {
            "session_id": session_id,
            "mode": config.mode,
            "netns_name": "cds-netns-a155",
            "iptables_chain": "cds-egress-a155",
            "dns_proxy_port": 15353,
        }

    _fake_engine(monkeypatch, host_side_policy)
    session_id = await _running_session(
        client, auth_headers, publish_product, monkeypatch, f"A155H-{uuid.uuid4().hex[:6]}"
    )

    resp = await client.put(
        f"/api/v1/sandbox-sessions/{session_id}/network-policy",
        json={"mode": "allowlist", "allowed_ips": ["10.0.0.0/8"], "active": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    enf = resp.json()["enforcement"]
    assert enf["netns_name"] == "cds-netns-a155"
    assert enf["enforced"] is False, enf
    assert enf["allowlist_in_force"] is False, enf
    assert enf["effective_mode"] == "deny_all", enf
    assert "A155" in enf["disclosure"]

    rows = (
        (
            await db_session.execute(
                select(AuditLog)
                .where(AuditLog.session_id == uuid.UUID(str(session_id)))
                .where(AuditLog.action == "sandbox.update_network_policy")
            )
        )
        .scalars()
        .all()
    )
    assert rows, "no update audit"
    detail = rows[-1].detail or {}
    assert detail["enforcement"]["enforced"] is False, detail
    assert detail["enforcement"]["effective_mode"] == "deny_all", detail


async def test_dataplane_pushed_policy_is_reported_as_enforced(
    client: AsyncClient,
    auth_headers: dict,
    monkeypatch: pytest.MonkeyPatch,
    publish_product,
) -> None:
    """A155 的另一侧：数据面确认过推送的策略才允许声明已执行。"""
    async def dataplane_policy(session_id: str, config, db=None):
        return {
            "session_id": session_id,
            "mode": config.mode,
            "enforced_by": "cube_dataplane",
            "policy_version": 7,
        }

    _fake_engine(monkeypatch, dataplane_policy)
    session_id = await _running_session(
        client, auth_headers, publish_product, monkeypatch, f"A155D-{uuid.uuid4().hex[:6]}"
    )

    resp = await client.put(
        f"/api/v1/sandbox-sessions/{session_id}/network-policy",
        json={"mode": "allowlist", "allowed_ips": ["10.0.0.0/8"], "active": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    enf = resp.json()["enforcement"]
    assert enf["enforced"] is True, enf
    assert enf["allowlist_in_force"] is True, enf
    assert enf["effective_mode"] == "allowlist", enf
