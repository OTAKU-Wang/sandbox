"""OCR 修复轮 — 联邦连接器四条（A150 / A60 / A61 / A63）。

台账（ocr-verified-findings.md）逐条钉住的不变量：

* **A150** 动态信任门的阈值表 `.get(operation, 0)`：`operation` 由请求体决定，
  任何未登记的名字（"query"/"READ"/拼写变体）都拿到阈值 0，门形同虚设。
  不变量：未登记 operation 取**表内最严**阈值（fail-closed），已登记的低风险
  op 在默认分（四维 50）下必须照常放行 —— 防止「过度修正」把正常路径关掉。
* **A60** 未校验的 `endpoint` = 带联邦 JWT 的 SSRF。不变量：建立信任时拒掉
  非 https / IP 字面量内网地址 / 元数据地址 / userinfo / 内部后缀域名；
  并且**请求时**再解析一次，解析到内网一律拒（域名指向 127.0.0.1 也挡住）。
* **A61** `send_request` 是同步 `httpx.Client`（read=30s）却跑在 `async def`
  调用点里。不变量：存在 `send_request_async`，阻塞体在**别的线程**上跑，
  事件循环不被占用；并且从工作线程回投的持久化仍然落在原事件循环上。
* **A63** 重启后 `trust_id` 不再解析（吊销开关静默 404）。不变量：不透明
  `trust-xxx` ↔ DB 主键 UUID 是**双向可逆**的，装载回来的信任仍能用创建时
  返回的那个 id 吊销。
"""
import asyncio
import re
import threading
import uuid
from unittest.mock import patch

import pytest

import app.services.federation_connector as fc
from app.services.federation_connector import (
    FederationConnector,
    FederationStatus,
    FederationTrust,
    GatewayRequest,
    GatewayResponse,
    SpaceIdentity,
    TrustLevel,
)

REPO = __import__("pathlib").Path(__file__).resolve().parents[1]

REMOTE = "cds-remote"


def _remote(endpoint: str = "https://remote.cds.example.com") -> SpaceIdentity:
    return SpaceIdentity(space_id=REMOTE, space_name="Remote CDS", endpoint=endpoint)


def _connector(endpoint: str = "https://local.cds.example.com") -> FederationConnector:
    connector = FederationConnector()
    connector.set_local_space(SpaceIdentity(space_id="cds-local", space_name="Local CDS", endpoint=endpoint))
    return connector


def _ok(self, request, trust):  # patched FederationConnector._execute_remote_request
    return GatewayResponse(request_id=request.request_id, status_code=200, data={"ok": True})


def _http_ok(self, request, url, base_url, body, mtls):  # patched _http_post
    return GatewayResponse(request_id=request.request_id, status_code=200, data={"ok": True})


# ── fake persistence (no real DB) ──────────────────────────────────────

class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _Store:
    def __init__(self):
        self.rows: list = []
        self.added: list = []

    def session_factory(self):
        store = self

        class _FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def execute(self, _stmt):
                return _FakeResult(store.rows)

            def add(self, record):
                store.added.append(record)

            async def commit(self):
                pass

        return _FakeSession


# ── A150: unregistered operation must fail closed ─────────────────────

class TestA150TrustGateFailClosed:
    def test_unregistered_operation_denied(self):
        connector = _connector()
        trust = connector.establish_trust(_remote(), allowed_operations=["query"])
        with patch.object(FederationConnector, "_execute_remote_request", _ok):
            response = connector.send_request(trust, "query", "/data")
        assert response.status_code == 403, response.error
        assert "query" in (response.error or "")

    def test_unregistered_operation_takes_strictest_threshold(self):
        connector = _connector()
        strictest = max(connector._min_trust_by_op.values())
        assert connector._min_trust_for("some-future-verb") == strictest
        assert connector._min_trust_for("") == strictest
        assert strictest >= 80

    def test_case_variant_falls_back_on_registered_threshold(self):
        """`READ` must not be a free pass — it scores against `read` (30)."""
        connector = _connector()
        assert connector._min_trust_for("READ") == connector._min_trust_by_op["read"]
        trust = connector.establish_trust(_remote(), allowed_operations=["READ"])
        connector.set_trust_scores(REMOTE, quality=10, compliance=10, reputation=10, security=10)
        with patch.object(FederationConnector, "_execute_remote_request", _ok):
            response = connector.send_request(trust, "READ", "/data")
        assert response.status_code == 403, response.error

    @pytest.mark.parametrize("op", ["search", "read_catalog", "verify_audit", "read", "write"])
    def test_registered_operations_still_pass_at_default_score(self, op):
        """Over-tightening guard: default scores (50) clear the registered ops."""
        connector = _connector()
        trust = connector.establish_trust(_remote(), allowed_operations=[op])
        with patch.object(FederationConnector, "_execute_remote_request", _ok):
            response = connector.send_request(trust, op, "/x")
        assert response.status_code == 200, f"{op}: {response.error}"

    def test_registered_high_risk_op_still_gated(self):
        connector = _connector()
        trust = connector.establish_trust(_remote(), allowed_operations=["admin"])
        with patch.object(FederationConnector, "_execute_remote_request", _ok):
            response = connector.send_request(trust, "admin", "/x")
        assert response.status_code == 403, response.error


# ── A60: endpoint is attacker-supplied — validate before dialling ─────

UNSAFE_ENDPOINTS = [
    "http://remote.example.com",                       # cleartext: bypasses mTLS verify entirely
    "https://169.254.169.254",                         # cloud metadata
    "https://169.254.169.254/latest/meta-data/",
    "https://127.0.0.1:8200",
    "https://localhost:6379",
    "https://LOCALHOST:8200",
    "https://[::1]:8200",
    "https://10.0.0.7",
    "https://192.168.1.5:8443",
    "https://172.16.0.9",
    "https://100.64.0.1",                              # CGNAT / shared
    "https://[fd00::1]",                               # IPv6 unique-local
    "https://metadata.google.internal",
    "https://redis.service.consul",
    "https://db.svc.cluster.local:6379",
    "https://user:pass@remote.example.com",            # credentials in URL
    "remote.example.com",                              # no scheme
    "ftp://remote.example.com",
    "https://",
    "https://remote.example.com/x?a=b",
    "https://2130706433",                              # decimal 127.0.0.1
    "https://0177.0.0.1",                              # octal 127.0.0.1
]

SAFE_ENDPOINTS = [
    "https://remote.cds.example.com",
    "https://partner.example.com:8443",
    "https://fed.example.com/federation",
    "https://FED2.Example.COM/",
]


class TestA60EndpointValidation:
    @pytest.mark.parametrize("endpoint", UNSAFE_ENDPOINTS)
    def test_establish_trust_rejects_unsafe_endpoint(self, endpoint):
        connector = _connector()
        with pytest.raises(ValueError):
            connector.establish_trust(_remote(endpoint=endpoint))

    @pytest.mark.parametrize("endpoint", SAFE_ENDPOINTS)
    def test_establish_trust_accepts_public_https(self, endpoint):
        connector = _connector()
        trust = connector.establish_trust(_remote(endpoint=endpoint))
        assert trust.status == FederationStatus.ACTIVE

    def test_validator_is_reachable_for_callers(self):
        assert fc.validate_federation_endpoint("https://fed.example.com").startswith("https://fed.example.com")
        with pytest.raises(ValueError):
            fc.validate_federation_endpoint("http://fed.example.com")

    def test_request_time_resolver_guard_refuses_internal_resolution(self, monkeypatch):
        """A public name that resolves to loopback must never be dialled."""
        connector = _connector()
        trust = connector.establish_trust(_remote(endpoint="https://partner.example.com"))
        request = GatewayRequest(
            request_id="req-1", source_space="cds-local", target_space=REMOTE,
            operation="read", resource="/data",
        )
        dialed: list = []
        monkeypatch.setattr(fc, "_resolve_addresses", lambda host: ["127.0.0.1"])
        monkeypatch.setattr(fc.httpx, "Client", lambda *a, **k: dialed.append(1))
        response = connector._execute_remote_request(request, trust)
        assert response.status_code == 403, response.error
        assert dialed == []

    def test_request_time_guard_lets_public_resolution_through(self, monkeypatch):
        connector = _connector()
        trust = connector.establish_trust(_remote(endpoint="https://partner.example.com"))
        request = GatewayRequest(
            request_id="req-2", source_space="cds-local", target_space=REMOTE,
            operation="read", resource="/data",
        )
        monkeypatch.setattr(fc, "_resolve_addresses", lambda host: ["93.184.216.34"])
        with patch.object(FederationConnector, "_http_post", _http_ok):
            response = connector._execute_remote_request(request, trust)
        assert response.status_code == 200

    def test_legacy_db_loaded_trust_is_validated_at_request_time(self, monkeypatch):
        """A trust row written before the guard existed still cannot dial out."""
        trust = FederationTrust(
            trust_id="trust-legacy",
            local_space=SpaceIdentity(space_id="cds-local", space_name="L", endpoint="https://local.example.com"),
            remote_space=_remote(endpoint="http://127.0.0.1:6379"),
            trust_level=TrustLevel.BASIC,
            status=FederationStatus.ACTIVE,
            allowed_operations=["read"],
        )
        request = GatewayRequest(
            request_id="req-3", source_space="cds-local", target_space=REMOTE,
            operation="read", resource="/",
        )
        dialed: list = []
        monkeypatch.setattr(fc.httpx, "Client", lambda *a, **k: dialed.append(1))
        response = _connector()._execute_remote_request(request, trust)
        assert response.status_code == 403, response.error
        assert dialed == []


# ── A61: the blocking client must not run on the event loop ───────────

class TestA61EventLoopSafety:
    @pytest.mark.asyncio
    async def test_send_request_async_runs_blocking_body_off_loop(self):
        connector = _connector()
        trust = connector.establish_trust(_remote(), allowed_operations=["search"])
        release = threading.Event()
        started = threading.Event()
        worker: list = []

        def slow(self, request, trust_):
            worker.append(threading.current_thread().name)
            started.set()
            release.wait(10)
            return GatewayResponse(request_id=request.request_id, status_code=200, data={})

        current = threading.current_thread().name
        task = asyncio.create_task(connector.send_request_async(trust, "search", "/data"))
        # Waiting for the worker in *another* thread keeps this coroutine honest:
        # if the connector blocked the loop, `started` would never be observed
        # before the timeout below.
        await asyncio.wait_for(asyncio.to_thread(started.wait, 5), timeout=8)
        progress = False

        async def probe():
            nonlocal progress
            progress = True

        await asyncio.wait_for(probe(), timeout=2)
        release.set()
        response = await asyncio.wait_for(task, timeout=10)

        assert progress is True
        assert worker and worker[0] != current
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_sync_entry_point_still_works(self):
        connector = _connector()
        trust = connector.establish_trust(_remote(), allowed_operations=["search"])
        with patch.object(FederationConnector, "_execute_remote_request", _ok):
            response = connector.send_request(trust, "search", "/data")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_persistence_from_worker_thread_lands_on_the_origin_loop(self):
        connector = _connector()
        store = _Store()
        connector.set_db_session_factory(store.session_factory())
        entry = fc.FederationAuditEntry(
            entry_id="audit-1", request_id="req-1", source_space="cds-local",
            target_space=REMOTE, operation="read", resource="/x", status="sent",
        )
        future = await asyncio.to_thread(
            connector._fire, connector._persist_audit_entry(entry), asyncio.get_running_loop(),
        )
        assert future is not None
        await asyncio.wait_for(asyncio.wrap_future(future), timeout=5)
        assert store.added and store.added[-1].entry_id == "audit-1"

    def test_async_call_sites_do_not_call_the_blocking_entry_point(self):
        for rel in ("app/api/federation.py", "app/services/catalog_sync.py"):
            src = (REPO / rel).read_text(encoding="utf-8")
            assert "send_request_async(" in src, f"{rel} still blocks the event loop"
            assert not re.search(r"(?<!_)send_request\(", src.replace("send_request_async(", "")), rel


# ── A63: opaque trust id must survive a restart ───────────────────────

class TestA63TrustIdRoundTrip:
    @pytest.mark.asyncio
    async def test_trust_id_created_now_is_revocable_after_restart(self):
        connector = _connector()
        store = _Store()
        connector.set_db_session_factory(store.session_factory())
        trust = connector.establish_trust(_remote())
        await connector._persist_trust(trust)
        assert len(store.added) == 1, "trust was not persisted (id derivation failed)"
        row = store.added[0]

        reloaded = _connector()
        store.rows = [row]
        reloaded.set_db_session_factory(store.session_factory())
        assert await reloaded.load_trusts_from_db() == 1

        assert reloaded.get_trust(trust.trust_id) is not None, (
            f"POST /trust returned {trust.trust_id}, after restart it 404s"
        )
        assert reloaded.revoke_trust(trust.trust_id) is True

    @pytest.mark.asyncio
    async def test_id_mapping_is_a_bijection(self):
        connector = _connector()
        trust = connector.establish_trust(_remote())
        row_id = fc.trust_row_id(trust.trust_id)
        assert isinstance(row_id, uuid.UUID)
        assert fc.opaque_trust_id(row_id) == trust.trust_id
        assert fc.trust_row_id(fc.opaque_trust_id(row_id)) == row_id

    def test_legacy_hyphenated_id_still_resolves(self):
        """Rows loaded by the previous loader carried str(row.id) as the id."""
        raw = uuid.uuid4()
        assert fc.trust_row_id(str(raw)) == raw
        assert fc.trust_row_id(raw.hex) == raw

    def test_legacy_padded_row_id_maps_back_to_the_short_form(self):
        """Rows written by the old `.ljust(32,'0')` code keep their old id."""
        short = f"trust-{uuid.uuid4().hex[:12]}"
        row_id = fc.trust_row_id(short)
        assert row_id.hex.endswith("0" * 20)
        assert fc.opaque_trust_id(row_id) == short
