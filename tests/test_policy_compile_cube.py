"""SS-10 CB-04 — egress policy compiler: policy package → CubeNetworkConfig.

Covers: proto field-level assertions (default-deny, allow_out, inject,
priority order), the full-state invariant (identical recompiles, no-incremental
rule removal), the l2c branch (zero netns/iptables side effects), the L0
regression (byte-identical iptables/netns command set), the hot-swap
Update(CubeNetworkConfig) push, and the CB-05 CA-bake probe.
"""
import subprocess
import uuid

import pytest

from sqlalchemy import select

from app._stubs.cube.cubebox.v1 import cubebox_pb2
from app.core.errors import CDSError
from app.models.data_product import DataProduct
from app.models.sandbox_node import SandboxNode
from app.models.sandbox_session import SandboxSession
from app.models.user import User
from app.services import cube_egress, cube_node_client
from app.services.network_policy import NetworkPolicyConfig, NetworkPolicyEngine
from app.services.policy_compiler import (
    compile_cube_network,
    policy_compiler,
    verify_cube_network_full_state,
)

DOLLAR = chr(36)
EXPECTED_INJECT_FORMAT = f"Bearer {DOLLAR}{{SECRET}}"
FULL_STATE_FIELDS = policy_compiler.CUBE_NETWORK_FULL_STATE_FIELDS
CUBE_EGRESS_NODE_PATH = cube_egress.CUBE_EGRESS_CA_NODE_PATH


async def _mk_session(
    db_session, sandbox_level: str = "l2c", container_id: str | None = None,
    contract_id: str | None = None,
) -> SandboxSession:
    unique = uuid.uuid4().hex[:8]
    user = User(
        username=f"cb04_{unique}",
        email=f"cb04_{unique}@example.com",
        hashed_password="x",
        role="data_provider",
    )
    db_session.add(user)
    await db_session.flush()
    product = DataProduct(provider_id=user.id, name=f"cb04-product-{unique}")
    db_session.add(product)
    await db_session.flush()
    session = SandboxSession(
        user_id=user.id,
        data_product_id=product.id,
        sandbox_level=sandbox_level,
        status="running",
        container_id=container_id,
        contract_id=contract_id,
    )
    db_session.add(session)
    # COMMIT (not just flush): keeps the fixture rows durable on the shared
    # in-memory test DB (the engine now runs on the caller's session, but
    # committed rows stay deterministic for every consumer).
    await db_session.commit()
    return session


# =============================================================================
# 1. Compiler — proto field-level assertions
# =============================================================================


def test_default_deny_and_whitelist():
    config = compile_cube_network({
        "allowed_ips": ["10.0.0.0/8", "8.8.8.8"],
        "allowed_domains": ["api.example.com"],
    })
    # default-deny baseline: explicitly set, never widened
    assert config.HasField("allow_internet_access")
    assert config.allow_internet_access is False
    # allow_out = ip/cidr entries + dns entries (ip first, order preserved)
    assert list(config.allow_out) == ["10.0.0.0/8", "8.8.8.8", "api.example.com"]
    assert list(config.deny_out) == []
    assert list(config.rules) == []
    verify_cube_network_full_state(config)


def test_rules_priority_order_preserved():
    pkg = {"rules": [
        {"name": "r1", "domain": "a.com", "path": "/v1", "methods": ["GET", "POST"]},
        {"name": "r2", "host": "b.com", "method": "PUT"},
        {"name": "r3", "domain": "c.com"},
        {"name": "r4", "domain": "d.com", "path": "/x", "methods": ["DELETE"]},
    ]}
    config = compile_cube_network(pkg)
    # first-match-wins priority = policy package rule order (stable, preserved)
    assert [r.name for r in config.rules] == ["r1", "r2", "r3", "r4"]
    # AND matching: every given match field is present on the rule
    r1, r2, r3, r4 = config.rules
    assert r1.match.host == "a.com"
    assert r1.match.path == "/v1"
    assert list(r1.match.method) == ["GET", "POST"]
    assert r2.match.host == "b.com"
    assert list(r2.match.method) == ["PUT"]
    assert r2.HasField("match")
    # host-only rule: path absent
    assert r3.match.host == "c.com"
    assert not r3.match.HasField("path")
    assert r4.match.path == "/x"
    assert all(r.action.allow is True for r in (r1, r2, r3, r4))


def test_credential_injection_entries():
    config = compile_cube_network(
        {"rules": [{"name": "r1", "domain": "api.example.com", "path": "/data"}]},
        credentials=[
            {"credential_id": "cred-1", "domain": "api.example.com", "secret": "s3cr3t"},
            {"credential_id": "cred-2", "domain": "other.com", "secret": "zz",
             "header": "X-Api-Key", "format": "raw"},
        ],
    )
    # credential matching an existing rule attaches to THAT rule
    assert list(config.rules[0].action.inject) and len(config.rules[0].action.inject) == 1
    inj = config.rules[0].action.inject[0]
    assert inj.header == "Authorization"
    assert inj.secret == "s3cr3t"
    # format = verified upstream placeholder (${SECRET}, NOT {{secret}})
    assert inj.format == EXPECTED_INJECT_FORMAT
    # credential without a matching rule → synthetic allow rule appended AFTER
    synth = [r for r in config.rules if r.name == "inject-cred-2"]
    assert len(synth) == 1
    assert synth[0].match.host == "other.com"
    assert synth[0].action.allow is True
    assert synth[0].action.inject[0].header == "X-Api-Key"
    assert synth[0].action.inject[0].secret == "zz"
    assert synth[0].action.inject[0].format == "raw"
    assert [r.name for r in config.rules] == ["r1", "inject-cred-2"]


def test_credential_incomplete_is_skipped_not_fabricated():
    config = compile_cube_network(
        {"rules": [{"name": "r1", "domain": "a.com"}],
         "credentials": [
             {"credential_id": "cred-empty", "domain": "a.com", "secret": ""},
             {"credential_id": "cred-nodomain", "secret": "x"},
         ]},
    )
    assert list(config.rules[0].action.inject) == []
    assert [r.name for r in config.rules] == ["r1"]


def test_internet_access_cannot_be_widened():
    with pytest.raises(CDSError) as exc:
        compile_cube_network({"allow_internet_access": True})
    assert exc.value.code == "POLICY_COMPILE_ERROR"


def test_rule_without_match_constraint_refused():
    with pytest.raises(CDSError) as exc:
        compile_cube_network({"rules": [{"name": "matches-everything"}]})
    assert exc.value.code == "POLICY_COMPILE_ERROR"


# =============================================================================
# 2. Full-state invariant (spec §2.8 — no incremental output)
# =============================================================================


def test_full_state_invariant_identical_recompiles():
    pkg = {
        "allowed_ips": ["1.2.3.4"],
        "allowed_domains": ["a.com"],
        "deny_out": ["9.9.9.9/32"],
        "rules": [
            {"name": "r1", "domain": "a.com", "path": "/x", "methods": ["GET"]},
            {"name": "r2", "domain": "b.com"},
        ],
    }
    first, second = compile_cube_network(pkg), compile_cube_network(dict(pkg))
    # identical input → identical field set AND identical bytes
    assert first.SerializeToString() == second.SerializeToString()
    desc = {f.name for f in cubebox_pb2.CubeNetworkConfig.DESCRIPTOR.fields}
    for field_name in FULL_STATE_FIELDS:
        assert field_name in desc
        assert field_name in cubebox_pb2.CubeNetworkConfig.DESCRIPTOR.fields_by_name


def test_full_state_rule_removal_no_incremental():
    pkg = {"rules": [
        {"name": "r1", "domain": "a.com"},
        {"name": "r2", "domain": "b.com"},
        {"name": "r3", "domain": "c.com", "path": "/p"},
    ]}
    before = compile_cube_network(pkg)
    assert [r.name for r in before.rules] == ["r1", "r2", "r3"]
    # remove r2 → a fresh compile no longer contains it (no merge/diff path)
    after = compile_cube_network({"rules": pkg["rules"][:1] + pkg["rules"][2:]})
    assert [r.name for r in after.rules] == ["r1", "r3"]
    # and a full removal clears the field to the empty (but present) state
    cleared = compile_cube_network({"rules": []})
    assert list(cleared.rules) == []
    assert cleared.allow_internet_access is False


# =============================================================================
# 3. l2c branch — zero netns/iptables side effects
# =============================================================================


@pytest.mark.asyncio
async def test_l2c_create_policy_zero_netns_iptables_side_effects(db_session, monkeypatch):
    session = await _mk_session(db_session, sandbox_level="l2c")

    calls = {"n": 0}

    def counting_run(*args, **kwargs):
        calls["n"] += 1
        return subprocess.CompletedProcess(args[0] if args else [], 0, stdout=b"", stderr=b"")

    pushed = []

    async def fake_push(session_id, config, *, default_deny=False, db=None):
        pushed.append((session_id, config.mode, default_deny))
        return {"applied": True}

    monkeypatch.setattr(cube_egress, "apply_network_policy_to_cube", fake_push)
    import app.services.network_policy as np_module
    monkeypatch.setattr(np_module.subprocess, "run", counting_run)

    engine = NetworkPolicyEngine()
    result = await engine.create_policy(
        str(session.id),
        NetworkPolicyConfig(mode="allowlist", allowed_ips=["10.0.0.0/8"]),
        db=db_session,
    )

    # zero host-side machinery: no `ip netns` / `iptables` invocation at all
    assert calls["n"] == 0
    assert result["enforced_by"] == "cube_dataplane"
    assert result["netns_name"] is None
    assert result["iptables_chain"] is None
    # the hot-swap push was the enforcement path
    assert pushed == [(str(session.id), "allowlist", False)]

    # removal: also zero host side effects, pushes the default-deny state
    await engine.remove_policy(str(session.id), db=db_session)
    assert calls["n"] == 0
    assert pushed[-1] == (str(session.id), "deny_all", True)


@pytest.mark.asyncio
async def test_engine_does_not_wipe_uncommitted_caller_rows(db_session):
    """Regression (CB-04 fix round 1): the engine must run the l2c check on
    the caller's session — never on a second session. A second session on
    the shared in-memory test connection rolls back (wipes) the caller's
    uncommitted rows on close; that is exactly what turned the e2e flows
    into 401s ("User not found or inactive").
    """
    session = await _mk_session(db_session, sandbox_level="l3")

    # an uncommitted row in the SAME (request) session — the mid-request
    # state of the session-create handler
    user = User(
        username=f"cb04_uncommitted_{uuid.uuid4().hex[:8]}",
        email=f"cb04_uncommitted_{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        role="data_provider",
    )
    db_session.add(user)
    await db_session.flush()

    engine = NetworkPolicyEngine()
    await engine.create_policy(
        str(session.id), NetworkPolicyConfig(mode="deny_all"), db=db_session,
    )
    await engine.remove_policy(str(session.id), db=db_session)

    # the uncommitted user must still be visible in the caller session —
    # a second-session open/close would have rolled the connection back
    row = (await db_session.execute(
        select(User).where(User.id == user.id)
    )).scalar_one()
    assert row.username == user.username


# =============================================================================
# 4. L0 regression — byte-identical iptables/netns command set
#
# Snapshot captured from the pre-change engine output for a sample policy
# (fresh engine, session id 11111111-2222-3333-4444-555555555555, level l3,
# allowlist 10.0.0.0/8:443, no dns proxy).
# =============================================================================

L0_SESSION_ID = "11111111-2222-3333-4444-555555555555"
L0_NETNS = f"cds-{L0_SESSION_ID[:12]}"
L0_CHAIN = f"CDS-{L0_NETNS}"
L0_VETH_HOST = f"vh-{L0_SESSION_ID[:10]}"
L0_VETH_SANDBOX = f"vs-{L0_SESSION_ID[:10]}"
EXPECTED_L0_CREATE_COMMANDS = [
    ["ip", "netns", "add", L0_NETNS],
    ["ip", "link", "add", L0_VETH_HOST, "type", "veth", "peer", "name", L0_VETH_SANDBOX],
    ["ip", "link", "set", L0_VETH_SANDBOX, "netns", L0_NETNS],
    ["ip", "addr", "add", "10.66.0.1/24", "dev", L0_VETH_HOST],
    ["ip", "link", "set", L0_VETH_HOST, "up"],
    ["ip", "netns", "exec", L0_NETNS, "ip", "addr", "add", "10.66.0.2/24", "dev", L0_VETH_SANDBOX],
    ["ip", "netns", "exec", L0_NETNS, "ip", "link", "set", L0_VETH_SANDBOX, "up"],
    ["ip", "netns", "exec", L0_NETNS, "ip", "link", "set", "lo", "up"],
    ["ip", "netns", "exec", L0_NETNS, "ip", "route", "add", "default", "via", "10.66.0.1"],
    ["sysctl", "-w", f"net.ipv4.conf.{L0_VETH_HOST}.forwarding=1"],
    ["iptables", "-t", "nat", "-A", "POSTROUTING", "-s", "10.66.0.0/24", "-j", "MASQUERADE"],
    ["ip", "netns", "exec", L0_NETNS, "iptables", "-N", L0_CHAIN],
    ["ip", "netns", "exec", L0_NETNS, "iptables", "-A", L0_CHAIN, "-m", "state", "--state", "ESTABLISHED,RELATED", "-j", "ACCEPT"],
    ["ip", "netns", "exec", L0_NETNS, "iptables", "-A", L0_CHAIN, "-d", "10.0.0.0/8", "-p", "tcp", "--dport", "443", "-j", "ACCEPT"],
    ["ip", "netns", "exec", L0_NETNS, "iptables", "-A", L0_CHAIN, "-o", "lo", "-j", "ACCEPT"],
    ["ip", "netns", "exec", L0_NETNS, "iptables", "-A", L0_CHAIN, "-j", "DROP"],
    ["ip", "netns", "exec", L0_NETNS, "iptables", "-A", "INPUT", "-j", L0_CHAIN],
    ["ip", "netns", "exec", L0_NETNS, "iptables", "-A", "OUTPUT", "-j", L0_CHAIN],
    ["ip", "netns", "exec", L0_NETNS, "iptables", "-A", "FORWARD", "-j", L0_CHAIN],
]
EXPECTED_L0_REMOVE_COMMANDS = [
    ["ip", "netns", "exec", L0_NETNS, "iptables", "-D", "INPUT", "-j", L0_CHAIN],
    ["ip", "netns", "exec", L0_NETNS, "iptables", "-D", "OUTPUT", "-j", L0_CHAIN],
    ["ip", "netns", "exec", L0_NETNS, "iptables", "-D", "FORWARD", "-j", L0_CHAIN],
    ["ip", "netns", "exec", L0_NETNS, "iptables", "-F", L0_CHAIN],
    ["ip", "netns", "exec", L0_NETNS, "iptables", "-X", L0_CHAIN],
    ["ip", "link", "del", L0_VETH_HOST],
    ["ip", "netns", "del", L0_NETNS],
    ["iptables", "-t", "nat", "-D", "POSTROUTING", "-s", "10.66.0.0/24", "-j", "MASQUERADE"],
]


@pytest.mark.asyncio
async def test_l0_path_byte_identical_iptables_ruleset(db_session, monkeypatch):
    # non-l2c session row → the engine takes the host path (regression guard)
    await _mk_session(db_session, sandbox_level="l3")

    commands: list[list[str]] = []

    def recording_run(cmd, *args, **kwargs):
        commands.append(cmd if isinstance(cmd, list) else str(cmd).split())
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    import app.services.network_policy as np_module
    monkeypatch.setattr(np_module.subprocess, "run", recording_run)

    engine = NetworkPolicyEngine()
    engine._ip_available = True
    engine._iptables_available = True

    result = await engine.create_policy(
        L0_SESSION_ID,
        NetworkPolicyConfig(
            mode="allowlist",
            allowed_ips=["10.0.0.0/8"],
            allowed_domains=[],
            allowed_ports=[443],
            dns_proxy_enabled=False,
        ),
        db=db_session,
    )
    assert result["netns_name"] == L0_NETNS
    assert result["iptables_chain"] == L0_CHAIN
    assert commands == EXPECTED_L0_CREATE_COMMANDS

    snapshot = list(commands)
    await engine.remove_policy(L0_SESSION_ID, db=db_session)
    assert commands[len(snapshot):] == EXPECTED_L0_REMOVE_COMMANDS


# =============================================================================
# 5. Hot-swap — Update(CubeNetworkConfig) full-state push for l2c
# =============================================================================


@pytest.mark.asyncio
async def test_hot_swap_pushes_full_state_update(db_session, monkeypatch):
    session = await _mk_session(
        db_session, sandbox_level="l2c", container_id="l2c-node1-" + "c" * 32,
    )
    node = SandboxNode(
        node_id="node1",
        hostname="cube-node-1",
        ip_address="10.1.1.1",
        status="online",
        capabilities={"cubelet_endpoint": "10.1.1.1:9443", "runtime_version": "9b75d33"},
    )
    db_session.add(node)
    await db_session.flush()

    updates = []

    class FakeClient:
        def update(self, request):
            updates.append(request)
            return cubebox_pb2.UpdateCubeSandboxResponse()

    monkeypatch.setattr(cube_node_client, "get_cube_node_client", lambda node, settings=None: FakeClient())

    result = await cube_egress.apply_network_policy_to_cube(
        str(session.id),
        NetworkPolicyConfig(mode="allowlist", allowed_ips=["1.2.3.4"],
                            allowed_domains=["api.example.com"]),
        db=db_session,
    )
    assert result["applied"] is True
    assert result["node_id"] == "node1"
    # SS-14 RT-07: sandbox_id = 尾部 32 位 hex（右切解析, D-e2e-19）
    assert result["sandbox_id"] == "c" * 32

    assert len(updates) == 1
    req = updates[0]
    assert req.sandboxID == "c" * 32
    # full-state: the request carries a populated CubeNetworkConfig
    assert req.HasField("cube_network_config")
    cfg = req.cube_network_config
    assert cfg.allow_internet_access is False
    assert list(cfg.allow_out) == ["1.2.3.4", "api.example.com"]
    assert list(cfg.rules) == []
    verify_cube_network_full_state(cfg)


@pytest.mark.asyncio
async def test_hot_swap_non_l2c_session_is_noop(db_session, monkeypatch):
    session = await _mk_session(db_session, sandbox_level="l3")
    result = await cube_egress.apply_network_policy_to_cube(
        str(session.id), NetworkPolicyConfig(mode="deny_all"),
        db=db_session,
    )
    assert result == {"applied": False, "reason": "not_l2c"}


@pytest.mark.asyncio
async def test_hot_swap_default_deny_push(db_session, monkeypatch):
    session = await _mk_session(
        db_session, sandbox_level="l2c", container_id="l2c-node1-" + "d" * 32,
    )
    node = SandboxNode(
        node_id="node1", hostname="cube-node-1", ip_address="10.1.1.1",
        status="online", capabilities={"cubelet_endpoint": "10.1.1.1:9443"},
    )
    db_session.add(node)
    await db_session.flush()

    updates = []

    class FakeClient:
        def update(self, request):
            updates.append(request)
            return cubebox_pb2.UpdateCubeSandboxResponse()

    monkeypatch.setattr(cube_node_client, "get_cube_node_client", lambda node, settings=None: FakeClient())

    result = await cube_egress.apply_network_policy_to_cube(
        str(session.id), NetworkPolicyConfig(mode="allowlist", allowed_ips=["10.0.0.0/8"]),
        default_deny=True, db=db_session,
    )
    assert result["applied"] is True
    cfg = updates[0].cube_network_config
    # zero-trust: policy removed ⇒ deny all (empty full state)
    assert cfg.allow_internet_access is False
    assert list(cfg.allow_out) == []
    assert list(cfg.rules) == []
    assert list(cfg.deny_out) == []


# =============================================================================
# 6. CA-bake probe (CB-05 template hook)
# =============================================================================


def test_ca_probe_missing_raises_honest_error(tmp_path):
    missing = tmp_path / "no-such-ca.crt"
    with pytest.raises(CDSError) as exc:
        cube_egress.check_cube_egress_ca_baked(str(missing))
    assert "CubeEgress root CA not found" in str(exc.value)
    assert CUBE_EGRESS_NODE_PATH in str(exc.value.detail)


def test_ca_probe_empty_file_raises(tmp_path):
    empty = tmp_path / "empty.crt"
    empty.write_bytes(b"")
    with pytest.raises(CDSError) as exc:
        cube_egress.check_cube_egress_ca_baked(str(empty))
    assert "empty" in str(exc.value)


def test_ca_probe_success(tmp_path):
    ca = tmp_path / "ca.crt"
    ca.write_bytes(b"FAKE-PEM-CA")
    probe = cube_egress.check_cube_egress_ca_baked(str(ca))
    assert probe["ca_path"] == str(ca)
    assert probe["size_bytes"] == len(b"FAKE-PEM-CA")
    assert probe["sha256"].startswith("sha256:")
