"""SS-12 L1 — API key unit tests (no HTTP): key format, SM3 lookup, scope
matrix (group x verb x * x presets x unmapped default-deny x human-only),
status machine, last_used throttle, brute-force cooldown, per-key rate limit.
"""
import base64
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core import scopes as ss12_scopes
from app.core.deps import _resolve_api_key
from app.core.errors import CDSError
from app.models.api_key import ApiKey
from app.models.user import User, UserRole
from app.utils.crypto import sm3_hash


class _FakeClient:
    host = "127.0.0.1"


class _FakeRequest:
    def __init__(self, path="/api/v1/meta/capability-matrix", method="GET"):
        self.client = _FakeClient()
        self.headers = {"user-agent": "ss12-test"}
        self.url = SimpleNamespace(path=path)
        self.method = method


@pytest.fixture(autouse=True)
def _clean_ss12_state():
    ss12_scopes.reset_auth_counters()
    ss12_scopes.reset_rate_limits()
    yield
    ss12_scopes.reset_auth_counters()
    ss12_scopes.reset_rate_limits()


@pytest.fixture
async def unit_session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def admin(unit_session):
    user = User(username="ss12-unit-admin", email="ss12-unit-admin@example.com",
                hashed_password="x", role=UserRole.ADMIN)
    unit_session.add(user)
    await unit_session.flush()
    return user


async def make_key(session, issuer, scopes, status="active", expires_at=None, rate_limit=120):
    bare = ApiKey.generate_bare_key()
    now = datetime.now(timezone.utc)
    key = ApiKey(
        name="unit-key", key_hash=sm3_hash(bare.encode()), key_prefix=ApiKey.display_prefix(bare),
        scopes=scopes, status=status, rate_limit=rate_limit, issued_by=issuer.id,
        created_at=now, updated_at=now, expires_at=expires_at,
    )
    session.add(key)
    await session.flush()
    return bare, key


# --- key format -------------------------------------------------------------

class TestKeyFormat:
    def test_bare_key_prefix_and_length(self):
        bare = ApiKey.generate_bare_key()
        assert bare.startswith("cds_ak_")
        assert len(bare) == 50  # 7-char prefix + 43-char base64url secret
        secret = bare[len("cds_ak_"):]
        assert len(secret) == 43
        # base64url alphabet, no padding
        assert all(c not in secret for c in "+/=")
        assert isinstance(base64.urlsafe_b64decode(secret + "="), bytes)

    def test_bare_keys_unique(self):
        assert len({ApiKey.generate_bare_key() for _ in range(200)}) == 200

    def test_display_prefix(self):
        bare = ApiKey.generate_bare_key()
        prefix = ApiKey.display_prefix(bare)
        assert prefix == bare[:11]
        assert prefix.startswith("cds_ak_")
        assert len(prefix) == 11  # "cds_ak_" + 4 chars


# --- SM3 hash lookup ---------------------------------------------------------

class TestSm3Lookup:
    @pytest.mark.asyncio
    async def test_hit_resolves_subject(self, unit_session, admin):
        bare, key = await make_key(unit_session, admin, ["meta:read"])
        subject = await _resolve_api_key(bare, unit_session, _FakeRequest())
        assert subject.kind == "api_key"
        assert subject.api_key.id == key.id
        # stored hash is the SM3 of the bare key
        assert key.key_hash == sm3_hash(bare.encode())

    @pytest.mark.asyncio
    async def test_miss_is_api_key_invalid_not_found(self, unit_session):
        with pytest.raises(CDSError) as ei:
            await _resolve_api_key("cds_ak_" + "a" * 43, unit_session, _FakeRequest())
        assert ei.value.code == "API_KEY_INVALID"
        assert ei.value.http_status == 401
        assert ei.value.detail["reason"] == "not_found"

    @pytest.mark.asyncio
    async def test_expired_key_is_invalid(self, unit_session, admin):
        past = datetime.now(timezone.utc) - timedelta(days=1)
        bare, _ = await make_key(unit_session, admin, ["meta:read"], expires_at=past)
        with pytest.raises(CDSError) as ei:
            await _resolve_api_key(bare, unit_session, _FakeRequest())
        assert ei.value.code == "API_KEY_INVALID"
        assert ei.value.detail["reason"] == "expired"


# --- scope matrix -------------------------------------------------------------

class TestScopeMatrix:
    @pytest.mark.parametrize("scopes,group,verb,expected", [
        (["meta:read"], "meta", "read", True),
        (["meta:read"], "meta", "write", False),
        (["meta:*"], "meta", "read", True),
        (["meta:*"], "meta", "write", True),
        (["sessions:read"], "meta", "read", False),
        ([], "meta", "read", False),
        (None, "meta", "read", False),
        (["data:read", "data:write"], "data", "write", True),
    ])
    def test_check_scope(self, scopes, group, verb, expected):
        assert ss12_scopes.check_scope(scopes, group, verb) is expected

    def test_method_verb_rule(self):
        assert ss12_scopes.method_verb("GET") == "read"
        for m in ("POST", "PATCH", "DELETE", "PUT"):
            assert ss12_scopes.method_verb(m) == "write"

    @pytest.mark.parametrize("path,expected", [
        ("/api/v1/meta/capability-matrix", "meta"),
        ("/api/v1/sandbox-sessions/abc/audit", "sessions"),
        ("/api/v1/sandbox-nodes", "infra"),
        ("/api/v1/sandbox-db/tables", "pipeline"),
        ("/api/v1/ops/nodes", "infra"),
        ("/metrics", "audit"),
        ("/health", "audit"),
        ("/api/v1/contracts", "contracts"),
        ("/api/v1/catalog", "data"),
        ("/api/v1/data-products", "data"),
        ("/api/v1/output-control", "output"),
        ("/api/v1/agent/runs", "agent"),
        ("/api/v1/federation/parties", "federation"),
        ("/api/v1/gateway", "gateway"),
        ("/api/v1/certificates", "certs"),
        ("/api/v1/kms/keys", "infra"),
    ])
    def test_resolve_group(self, path, expected):
        assert ss12_scopes.resolve_group(path) == expected

    def test_unmapped_prefix_default_deny(self):
        assert ss12_scopes.resolve_group("/api/v1/unknown-thing") is None
        assert ss12_scopes.check_scope(["meta:*"], "meta", "read")
        # key subject on unmapped prefix: no group -> denied
        group = ss12_scopes.resolve_group("/api/v1/unknown-thing")
        assert ss12_scopes.check_scope(["*"], group or "", "read") is False

    @pytest.mark.parametrize("path", [
        "/api/v1/auth/login", "/api/v1/users", "/api/v1/api-keys",
        "/internal/audit", "/static/app.js",
    ])
    def test_human_only_prefixes(self, path):
        assert ss12_scopes.is_human_only(path)
        assert ss12_scopes.resolve_group(path) is None

    def test_preset_readonly(self):
        scopes = ss12_scopes.expand_preset("readonly")
        assert sorted(scopes) == sorted(f"{g}:read" for g in ss12_scopes.REACHABLE_GROUPS)
        assert len(scopes) == 12

    def test_preset_operator(self):
        scopes = ss12_scopes.expand_preset("operator")
        expected_read = {f"{g}:read" for g in ss12_scopes.REACHABLE_GROUPS}
        expected_write = {f"{g}:write" for g in ("sessions", "contracts", "data", "pipeline", "output", "gateway", "agent")}
        assert expected_read.issubset(set(scopes))
        assert expected_write.issubset(set(scopes))
        assert "infra:write" not in scopes
        assert "meta:write" not in scopes

    def test_preset_full(self):
        scopes = ss12_scopes.expand_preset("full")
        assert sorted(scopes) == sorted(f"{g}:*" for g in ss12_scopes.REACHABLE_GROUPS)
        assert len(scopes) == 12

    def test_preset_custom_validated(self):
        assert ss12_scopes.expand_preset("custom", ["meta:read"]) == ["meta:read"]
        with pytest.raises(ValueError):
            ss12_scopes.expand_preset("custom", ["bogus:read"])
        with pytest.raises(ValueError):
            ss12_scopes.expand_preset("custom", None)
        with pytest.raises(ValueError):
            ss12_scopes.expand_preset("nope", None)

    @pytest.mark.asyncio
    async def test_human_only_rejects_key_subject(self, unit_session, admin):
        bare, _ = await make_key(unit_session, admin, ["meta:*"])
        req = _FakeRequest(path="/api/v1/api-keys", method="GET")
        subject = await _resolve_api_key(bare, unit_session, req)
        checker = ss12_scopes.require_human
        with pytest.raises(CDSError) as ei:
            await checker(req, subject)
        assert ei.value.code == "API_KEY_SCOPE_DENIED"
        assert ei.value.http_status == 403
        assert ei.value.detail["reason"] == "human_only"

    @pytest.mark.asyncio
    async def test_require_group_blocks_out_of_scope(self, unit_session, admin):
        bare, _ = await make_key(unit_session, admin, ["meta:read"])
        req = _FakeRequest(path="/api/v1/contracts", method="GET")
        subject = await _resolve_api_key(bare, unit_session, req)
        checker = ss12_scopes.require_group("contracts")
        with pytest.raises(CDSError) as ei:
            await checker(req, subject)
        assert ei.value.code == "API_KEY_SCOPE_DENIED"
        assert ei.value.detail == {"group": "contracts", "verb": "read", "path": "/api/v1/contracts"}

    @pytest.mark.asyncio
    async def test_require_group_passes_in_scope(self, unit_session, admin):
        bare, _ = await make_key(unit_session, admin, ["meta:read"])
        req = _FakeRequest(path="/api/v1/meta/capability-matrix", method="GET")
        subject = await _resolve_api_key(bare, unit_session, req)
        assert await ss12_scopes.require_group("meta")(req, subject) is None

    @pytest.mark.asyncio
    async def test_user_subject_bypasses_group_gate(self, unit_session):
        from app.core.deps import AuthSubject
        user = User(username="gate-user", email="gate-user@example.com",
                    hashed_password="x", role=UserRole.BUYER)
        unit_session.add(user)
        await unit_session.flush()
        req = _FakeRequest()
        subject = AuthSubject(kind="user", user=user)
        assert await ss12_scopes.require_group("contracts")(req, subject) is None
        assert await ss12_scopes.require_human(req, subject) is None


# --- status machine -------------------------------------------------------------

class TestStatusMachine:
    @pytest.mark.asyncio
    async def test_disabled_is_recoverable(self, unit_session, admin):
        bare, key = await make_key(unit_session, admin, ["meta:read"], status="disabled")
        with pytest.raises(CDSError) as ei:
            await _resolve_api_key(bare, unit_session, _FakeRequest())
        assert ei.value.code == "API_KEY_INACTIVE"
        assert ei.value.detail["reason"] == "disabled"
        # recoverable: re-activation works
        key.status = "active"
        await unit_session.flush()
        subject = await _resolve_api_key(bare, unit_session, _FakeRequest())
        assert subject.kind == "api_key"

    @pytest.mark.asyncio
    async def test_revoked_is_terminal_inactive(self, unit_session, admin):
        bare, _ = await make_key(unit_session, admin, ["meta:read"], status="revoked")
        with pytest.raises(CDSError) as ei:
            await _resolve_api_key(bare, unit_session, _FakeRequest())
        assert ei.value.code == "API_KEY_INACTIVE"
        assert ei.value.detail["reason"] == "revoked"


# --- last_used 60s throttle ------------------------------------------------------

class TestLastUsedThrottle:
    @pytest.mark.asyncio
    async def test_throttled_within_60s_updated_after(self, unit_session, admin):
        bare, key = await make_key(unit_session, admin, ["meta:read"])
        await _resolve_api_key(bare, unit_session, _FakeRequest())
        first = key.last_used_at
        assert first is not None
        await _resolve_api_key(bare, unit_session, _FakeRequest())
        assert key.last_used_at == first  # within 60s -> not rewritten

        key.last_used_at = datetime.now(timezone.utc) - timedelta(seconds=120)
        await unit_session.flush()
        await _resolve_api_key(bare, unit_session, _FakeRequest())
        assert key.last_used_at != first


# --- brute-force cooldown ---------------------------------------------------------

class TestCooldown:
    @pytest.mark.asyncio
    async def test_five_failures_trigger_15min_cooldown(self, unit_session):
        token = "cds_ak_" + "b" * 43
        for _ in range(4):
            with pytest.raises(CDSError):
                await _resolve_api_key(token, unit_session, _FakeRequest())
        assert ss12_scopes.cooldown_remaining_seconds(sm3_hash(token.encode())) == 0.0

        with pytest.raises(CDSError):
            await _resolve_api_key(token, unit_session, _FakeRequest())  # 5th failure arms it
        remaining = ss12_scopes.cooldown_remaining_seconds(sm3_hash(token.encode()))
        assert 0 < remaining <= ss12_scopes.COOLDOWN_SECONDS

        with pytest.raises(CDSError) as ei:
            await _resolve_api_key(token, unit_session, _FakeRequest())
        assert ei.value.code == "API_KEY_INACTIVE"
        assert ei.value.detail["reason"] == "cooldown"

    @pytest.mark.asyncio
    async def test_cooldown_expiry(self, unit_session):
        token = "cds_ak_" + "c" * 43
        h = sm3_hash(token.encode())
        ss12_scopes.record_auth_failure(h)
        ss12_scopes.record_auth_failure(h)
        ss12_scopes.record_auth_failure(h)
        ss12_scopes.record_auth_failure(h)
        ss12_scopes.record_auth_failure(h)
        assert ss12_scopes.cooldown_remaining_seconds(h) > 0
        # expiry: move the deadline into the past
        ss12_scopes._cooldown_until[h] = time.monotonic() - 1
        assert ss12_scopes.cooldown_remaining_seconds(h) == 0.0
        # and auth proceeds to the normal not-found path again
        with pytest.raises(CDSError) as ei:
            await _resolve_api_key(token, unit_session, _FakeRequest())
        assert ei.value.code == "API_KEY_INVALID"

    @pytest.mark.asyncio
    async def test_success_resets_failure_counter(self, unit_session, admin):
        bare, _ = await make_key(unit_session, admin, ["meta:read"])
        h = sm3_hash(bare.encode())
        for _ in range(4):
            ss12_scopes.record_auth_failure(h)
        assert ss12_scopes.cooldown_remaining_seconds(h) == 0.0
        await _resolve_api_key(bare, unit_session, _FakeRequest())
        assert ss12_scopes._auth_failures.get(h, 0) == 0
        for _ in range(4):
            ss12_scopes.record_auth_failure(h)
        assert ss12_scopes.cooldown_remaining_seconds(h) == 0.0  # consecutive reset held


# --- per-key rate limit -------------------------------------------------------------

class TestRateLimit:
    @pytest.mark.asyncio
    async def test_limit_enforced_with_429(self, unit_session, admin):
        bare, _ = await make_key(unit_session, admin, ["meta:read"], rate_limit=2)
        await _resolve_api_key(bare, unit_session, _FakeRequest())
        await _resolve_api_key(bare, unit_session, _FakeRequest())
        with pytest.raises(CDSError) as ei:
            await _resolve_api_key(bare, unit_session, _FakeRequest())
        assert ei.value.code == "RATE_LIMITED"
        assert ei.value.http_status == 429
        assert 1 <= ei.value.retry_after <= 60

    @pytest.mark.asyncio
    async def test_window_rolls_over(self, unit_session, admin):
        bare, key = await make_key(unit_session, admin, ["meta:read"], rate_limit=1)
        await _resolve_api_key(bare, unit_session, _FakeRequest())
        # simulate the counter landing in the previous minute
        key_id = str(key.id)
        minute, _count = ss12_scopes._key_rate_limits[key_id]
        ss12_scopes._key_rate_limits[key_id] = (minute - 1, 1)
        await _resolve_api_key(bare, unit_session, _FakeRequest())  # new window -> allowed


class TestAuthFailureCounterBound:
    """M1 (CR 2026-10-15): the brute-force tables are fed by *unauthenticated*
    traffic — an attacker spraying wrong keys must not be able to grow them
    without bound."""

    def test_failure_table_stays_bounded(self, monkeypatch):
        monkeypatch.setattr(ss12_scopes, "MAX_TRACKED_AUTH_KEYS", 100)
        for i in range(1000):
            ss12_scopes.record_auth_failure(f"spray-{i}")
        assert len(ss12_scopes._auth_failures) <= 100
        assert "spray-999" in ss12_scopes._auth_failures  # newest survives

    def test_cooldown_table_stays_bounded(self, monkeypatch):
        monkeypatch.setattr(ss12_scopes, "MAX_TRACKED_AUTH_KEYS", 100)
        for i in range(1000):
            for _ in range(ss12_scopes.COOLDOWN_FAILURE_THRESHOLD):
                ss12_scopes.record_auth_failure(f"armed-{i}")
        assert len(ss12_scopes._cooldown_until) <= 100

    def test_stale_entries_are_pruned(self, monkeypatch):
        monkeypatch.setattr(ss12_scopes, "MAX_TRACKED_AUTH_KEYS", 1000)
        ss12_scopes.record_auth_failure("old-key")
        seen = ss12_scopes._auth_failures["old-key"][1]
        ss12_scopes._auth_failures["old-key"] = (
            1, seen - ss12_scopes.COOLDOWN_SECONDS - 1)
        ss12_scopes.record_auth_failure("other-key")
        assert "old-key" not in ss12_scopes._auth_failures
        assert "other-key" in ss12_scopes._auth_failures
