"""A156（high）— `network_policy` 执行面四条 fail-open 腿：失败必须可见。

台账: docs/ocr-review-2026-10-18/ocr-verified-findings.md §A156
落点: app/services/network_policy.py
  ① `create_policy`：`if self._iptables_available and config.allowed_ips` —— 只配域名/端口
     不配 IP 的策略一条链也不建，netns 默认 ACCEPT ⇒ 等价全放行（链尾那条无条件 DROP 根本没挂上）。
  ② `_create_netns`：`subprocess.run(..., capture_output=True)` 全程不看 returncode，函数末尾
     无条件 `return True` ⇒ 非 root / netns 同名 / `10.66.0.0/24` 冲突 / `ip` 子命令不支持
     都会「失败但返回成功」，调用方拿着填好的 netns_name 去写审计。
  ③ `_start_dns_proxy`：绑宿主 `127.0.0.1:<10053+>`，既不是 53 也不是 netns 网关地址，
     netns 里的沙箱到不了 ⇒ 域名白名单实际未生效。真修需要宿主 veth 编址 + DNAT + guest
     resolv.conf（见证据 NEEDS-OWNER）；本文件钉的是「不得声称已生效」。
  ④ `get_dns_env_vars`：`port = self._dns_port_counter - 1` 取全局计数器，
     并发多会话时拿到的可能是别人的端口/别人的域名白名单。

runtime 侧（sandbox_runtime / firecracker_runtime / sandbox_security 的 resolv.conf 同构分支）
由 tests/test_ocr_a156_egress_fail_closed.py 与 NEEDS-OWNER 覆盖，不在本文件。
"""

from __future__ import annotations

import subprocess

import pytest

import app.services.network_policy as np_module
from app.services.network_policy import (
    DNSProxyProtocol,
    NetworkPolicyConfig,
    NetworkPolicyEngine,
)


class _IpCmdRecorder:
    """假 `subprocess.run`：记录 argv；命中 `failures` 任一子串的命令返回 rc=1。"""

    def __init__(self, failures: tuple[str, ...] = ()) -> None:
        self.failures = failures
        self.calls: list[str] = []

    def __call__(self, argv, *args, **kwargs):
        joined = " ".join(str(x) for x in argv)
        self.calls.append(joined)
        rc = 1 if any(f in joined for f in self.failures) else 0
        stderr = b"Operation not permitted" if rc else b""
        return subprocess.CompletedProcess(list(argv), rc, stdout=b"", stderr=stderr)

    def containing(self, needle: str) -> list[str]:
        return [c for c in self.calls if needle in c]


class _FakeTransport:
    def close(self) -> None:
        return None


async def _fake_datagram_endpoint(
    self, protocol_factory, transport_factory=None, *, local_addr=None, **kwargs
):
    """假 `loop.create_datagram_endpoint`：不真绑端口，返回真 protocol 实例。"""
    return _FakeTransport(), protocol_factory()


def _allowlist(
    allowed_ips: list[str] | None = None,
    allowed_domains: list[str] | None = None,
    allowed_ports: list[int] | None = None,
    dns_proxy_enabled: bool = False,
) -> NetworkPolicyConfig:
    return NetworkPolicyConfig(
        mode="allowlist",
        allowed_ips=allowed_ips if allowed_ips is not None else ["10.0.0.0/8"],
        allowed_domains=allowed_domains or [],
        allowed_ports=allowed_ports if allowed_ports is not None else [443],
        dns_proxy_enabled=dns_proxy_enabled,
    )


async def _fake_netns_ok(*args, **kwargs) -> bool:
    return True


@pytest.mark.asyncio
async def test_netns_failure_is_not_reported_as_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """腿②：`ip netns add` 失败（非 root）时 `_create_netns` 必须返回 False。"""
    engine = NetworkPolicyEngine()
    monkeypatch.setattr(np_module.subprocess, "run", _IpCmdRecorder(failures=("netns add",)))

    assert await engine._create_netns("cds-a156-a", "vh-a156a", "vs-a156a") is False, (
        "A156-2: ip 命令失败仍 return True——调用方会拿这个 netns_name 去写审计"
    )


@pytest.mark.asyncio
async def test_netns_midway_failure_rolls_back_partial_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """腿②：中途失败必须回滚已建的 netns/veth 并报告失败。"""
    engine = NetworkPolicyEngine()
    rec = _IpCmdRecorder(failures=("addr add",))
    monkeypatch.setattr(np_module.subprocess, "run", rec)

    assert await engine._create_netns("cds-a156-b", "vh-a156b", "vs-a156b") is False
    assert rec.containing("netns del") or rec.containing("link del"), (
        f"A156-2: 中途失败没有回滚已建资源，calls={rec.calls}"
    )


@pytest.mark.asyncio
async def test_create_policy_downgrades_honestly_when_netns_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """腿②：装配结果不得继续声称 allowlist 已生效，也不得填一个不存在的 netns_name。"""
    engine = NetworkPolicyEngine()
    monkeypatch.setattr(engine, "_ip_available", True)
    monkeypatch.setattr(np_module.subprocess, "run", _IpCmdRecorder(failures=("netns add",)))

    res = await engine.create_policy("a156-c", _allowlist())

    assert not res["netns_name"], f"A156-2: netns 没建成仍回报 netns_name={res['netns_name']!r}"
    assert res["mode"] == "deny_all", (
        f"A156-2: 降级为 deny_all 后结果仍回报 mode={res['mode']!r}（审计照着写 allowlist）"
    )
    assert res.get("degraded"), f"A156-2: 降级原因必须写进结果：{res}"
    active = engine.get_policy("a156-c")
    assert active is None or active.mode == "deny_all", (
        f"A156-2: 引擎状态表里留着 allowlist（运行期兜底会按 allowlist 放行）：{active}"
    )


@pytest.mark.asyncio
async def test_iptables_chain_is_built_even_without_allowed_ips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """腿①：只配域名/端口不配 IP 时也必须建链——链尾无条件 DROP 才是兜底。"""
    engine = NetworkPolicyEngine()
    monkeypatch.setattr(engine, "_ip_available", True)
    monkeypatch.setattr(engine, "_iptables_available", True)

    built: list[tuple[str, NetworkPolicyConfig]] = []

    def _fake_chain(netns_name: str, config: NetworkPolicyConfig) -> str:
        built.append((netns_name, config))
        return f"CDS-{netns_name}"

    monkeypatch.setattr(engine, "_create_netns", _fake_netns_ok)
    monkeypatch.setattr(engine, "_create_iptables_chain", _fake_chain)

    res = await engine.create_policy(
        "a156-d",
        _allowlist(allowed_ips=[], allowed_domains=["api.example.com"]),
    )

    assert built, "A156-1: allowed_ips 为空时一条 iptables 链也没建（netns 默认 ACCEPT = 全放行）"
    assert res["iptables_chain"] == "CDS-cds-a156-d", f"A156-1: 结果没带真实链名：{res}"
    assert res["mode"] == "allowlist", res


@pytest.mark.asyncio
async def test_chain_build_failure_is_reported_not_hidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """腿①：链没建成 = 一条 DROP 都没有，结果必须降级上报而不是继续报 allowlist。"""
    engine = NetworkPolicyEngine()
    monkeypatch.setattr(engine, "_ip_available", True)
    monkeypatch.setattr(engine, "_iptables_available", True)
    monkeypatch.setattr(engine, "_create_netns", _fake_netns_ok)
    monkeypatch.setattr(engine, "_create_iptables_chain", lambda *_a, **_k: "")

    res = await engine.create_policy("a156-e", _allowlist())

    assert not res["iptables_chain"]
    assert res["mode"] == "deny_all", f"A156-1: 链装配失败仍回报 mode={res['mode']!r}"
    assert res.get("degraded"), f"A156-1: 降级原因必须写进结果：{res}"


@pytest.mark.asyncio
async def test_domain_filtering_is_not_claimed_when_proxy_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """腿③：代理绑宿主 127.0.0.1 且不是 53 端口 ⇒ 域名过滤不得被报成已生效。"""
    engine = NetworkPolicyEngine()
    monkeypatch.setattr(engine, "_ip_available", True)
    monkeypatch.setattr(engine, "_iptables_available", True)
    monkeypatch.setattr(engine, "_create_netns", _fake_netns_ok)
    monkeypatch.setattr(engine, "_create_iptables_chain", lambda n, _c: f"CDS-{n}")

    monkeypatch.setattr(np_module.asyncio.BaseEventLoop, "create_datagram_endpoint", _fake_datagram_endpoint)

    res = await engine.create_policy(
        "a156-f", _allowlist(allowed_domains=["api.example.com"], dns_proxy_enabled=True)
    )

    assert res["dns_proxy_port"], "用例前提：代理确实被拉起了"
    assert res.get("domain_filtering") == "not-enforced", (
        f"A156-3: netns 到不了的代理被当成域名过滤已生效上报：{res}"
    )


@pytest.mark.asyncio
async def test_dns_env_vars_uses_this_sessions_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """腿④：`get_dns_env_vars` 必须取本会话自己的端口，而不是全局计数器最后一次。"""
    engine = NetworkPolicyEngine()

    monkeypatch.setattr(np_module.asyncio.BaseEventLoop, "create_datagram_endpoint", _fake_datagram_endpoint)

    port_a = await engine._start_dns_proxy("a156-s1", _allowlist(allowed_domains=["a.example.com"]))
    port_b = await engine._start_dns_proxy("a156-s2", _allowlist(allowed_domains=["b.example.com"]))
    assert port_a != port_b, "两个会话应当各自拿到一个端口"

    engine._policies["a156-s1"] = _allowlist(allowed_domains=["a.example.com"], dns_proxy_enabled=True)
    engine._policies["a156-s2"] = _allowlist(allowed_domains=["b.example.com"], dns_proxy_enabled=True)

    env_a = engine.get_dns_env_vars("a156-s1")
    env_b = engine.get_dns_env_vars("a156-s2")

    assert env_a and env_b, "两个会话都应拿到自己的 DNS 环境变量"
    assert str(port_a) in env_a["CDS_DNS_PROXY"], (
        f"A156-4: 会话1 拿到别人的端口：{env_a['CDS_DNS_PROXY']} (own={port_a} other={port_b})"
    )
    assert str(port_b) in env_b["CDS_DNS_PROXY"], (
        f"A156-4: 会话2 拿到别人的端口：{env_b['CDS_DNS_PROXY']} (own={port_b} other={port_a})"
    )


def test_dns_proxy_protocol_still_filters_domains():
    """对照：代理自身的域名过滤本来是对的（钉住不回归）。"""
    proto = DNSProxyProtocol(allowed_domains=["api.example.com"])
    assert proto._is_domain_allowed("api.example.com") is True
    assert proto._is_domain_allowed("evil.example.com") is False
