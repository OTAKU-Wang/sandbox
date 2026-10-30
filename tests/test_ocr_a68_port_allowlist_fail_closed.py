"""A68（medium，API 可达 / 安全级 fail-open）— 端口维度白名单为空 = 任何端口都放行。

台账: docs/ocr-review-2026-10-18/ocr-verified-findings.md ####A68
落点（三处同改）:
- `app/schemas/network_policy.py::_validate_ports`（Create `:49-53` / Update `:77-80` 共用）
- `app/services/network_policy.py::NetworkPolicyEngine.check_connection`（`:731`）
  与 `create_policy` 的 allowlist 入口
- `app/models/network_policy.py::is_port_allowed`（`:94-98`）

零信任口径：`mode=allowlist` 且 `allowed_ports == []` 是**自相矛盾**的策略——同文件
`is_ip_allowed` / `is_domain_allowed` 对空白名单一律 `return False`（默认拒绝），端口维度
却 `return True` / 跳过过滤。HEAD 实测（`.gate-scratch/evidence/backend-19a.md`）
`allowed_ports=[]` 时 22/13389 全部判 True。修法不许保留「空 = 不限」的兼容分支：
边界拒空列表并提示改用 `mode=deny_all`，执行面与模型 helper 一律 fail-closed。
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import sandbox_sessions as _sandbox_sessions  # noqa: F401  (import order guard)
from app.core.errors import CDSError
from app.models.network_policy import NetworkPolicy
from app.models.sandbox_session import SandboxSession, SessionStatus
from app.schemas.network_policy import NetworkPolicyCreate, NetworkPolicyUpdate
from app.services.network_policy import NetworkPolicyConfig, NetworkPolicyEngine

POLICY_URL = "/api/v1/network-policies"


def test_engine_denies_every_port_when_allowlist_ports_empty():
    engine = NetworkPolicyEngine()
    engine._policies["a68-session"] = NetworkPolicyConfig(
        mode="allowlist",
        allowed_ips=["10.0.0.0/8"],
        allowed_ports=[],
    )
    for port in (22, 443, 8080, 13389):
        assert engine.check_connection("a68-session", "10.0.0.5", port) is False, (
            f"A68: allowed_ports=[] 时端口 {port} 被判放行（fail-open）"
        )


@pytest.mark.asyncio
async def test_engine_create_policy_refuses_allowlist_without_ports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """装配面同样不得接受「allowlist 但一个端口都不允许」的配置。"""
    engine = NetworkPolicyEngine()
    # 主机侧动作（netns/iptables/DNS 代理）在本用例里一律不落宿主：
    # 它们各自由 A156 用例钉住。这里只关心装配是否拒收。
    async def _fake_netns(*_a, **_k):
        return True

    async def _fake_dns(*_a, **_k):
        return 10053

    monkeypatch.setattr(engine, "_create_netns", _fake_netns)
    monkeypatch.setattr(engine, "_create_iptables_chain", lambda *_a, **_k: "CDS-TEST")
    monkeypatch.setattr(engine, "_start_dns_proxy", _fake_dns)

    with pytest.raises(CDSError) as exc:
        await engine.create_policy(
            "a68-create",
            NetworkPolicyConfig(mode="allowlist", allowed_ips=["10.0.0.0/8"], allowed_ports=[]),
        )
    assert exc.value.code == "VALIDATION_ERROR", f"A68: 拒绝码应为 VALIDATION_ERROR，实际 {exc.value.code}"
    assert "deny_all" in str(exc.value), "拒绝理由必须指向 mode=deny_all"
    assert "a68-create" not in engine._policies, (
        "A68: 被拒绝的配置仍被写进了引擎状态表（执行面与记录面分叉）"
    )


def test_model_is_port_allowed_denies_when_empty():
    policy = NetworkPolicy(
        session_id="a68", user_id="u1", mode="allowlist", allowed_ports=[]
    )
    assert policy.is_port_allowed(443) is False, "A68: 空端口白名单的模型 helper 判放行"
    assert policy.is_port_allowed(22) is False


def test_model_ip_and_domain_helpers_stay_fail_closed():
    """对照：同文件 ip/domain 维度本来就是默认拒绝（本条应始终为绿，钉住不回归）。"""
    policy = NetworkPolicy(session_id="a68", user_id="u1", mode="allowlist")
    assert policy.is_ip_allowed("1.2.3.4") is False
    assert policy.is_domain_allowed("evil.example") is False


def test_model_port_allowlist_still_allows_listed_ports():
    policy = NetworkPolicy(
        session_id="a68", user_id="u1", mode="allowlist", allowed_ports=[443, 80]
    )
    assert policy.is_port_allowed(443) is True
    assert policy.is_port_allowed(80) is True
    assert policy.is_port_allowed(8080) is False


def test_schema_create_rejects_empty_allowed_ports():
    with pytest.raises(ValidationError) as exc:
        NetworkPolicyCreate(session_id="s1", mode="allowlist", allowed_ports=[])
    assert "deny_all" in str(exc.value), (
        f"A68: 拒绝文案要给出替代方案 mode=deny_all，实际 {exc.value}"
    )


def test_schema_update_rejects_empty_allowed_ports():
    with pytest.raises(ValidationError) as exc:
        NetworkPolicyUpdate(mode="allowlist", allowed_ports=[])
    assert "deny_all" in str(exc.value)


def test_schema_keeps_none_and_normal_lists():
    """`None`（Update 的「不改这一维」）与正常列表都不得被一起拒掉。"""
    assert NetworkPolicyUpdate(mode="allowlist", allowed_ports=None).allowed_ports is None
    assert NetworkPolicyUpdate(allowed_ports=[443]).allowed_ports == [443]
    assert NetworkPolicyCreate(session_id="s").allowed_ports == [443, 80]


async def _seed_session(db_session: AsyncSession, user_id: str) -> str:
    session = SandboxSession(
        id=uuid.uuid4(),
        user_id=uuid.UUID(user_id),
        data_product_id=uuid.uuid4(),
        sandbox_level="L3",
        sandbox_mode="structured_query",
        status=SessionStatus.RUNNING.value,
    )
    db_session.add(session)
    await db_session.flush()
    return str(session.id)


@pytest.mark.asyncio
async def test_api_rejects_empty_allowed_ports(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user,
) -> None:
    headers, uid = await make_user("data_provider", "a68api")
    sid = await _seed_session(db_session, uid)

    resp = await client.post(
        POLICY_URL,
        json={
            "session_id": sid,
            "mode": "allowlist",
            "allowed_ips": ["10.0.0.0/8"],
            "allowed_ports": [],
        },
        headers=headers,
    )
    assert resp.status_code == 422, (
        f"A68: mode=allowlist + allowed_ports=[] 应在边界被拒 422，"
        f"实际 {resp.status_code}: {resp.text[:200]}"
    )
    assert "deny_all" in resp.text, f"A68: 422 未提示改用 deny_all：{resp.text[:200]}"
