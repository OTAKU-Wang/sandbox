"""SS-12 L1 — API key management API tests (ASGI): create/list/rotate/patch/
delete semantics, role gates, human-only rejection of key subjects, scope
gate behavior, W2 error contract (4 fields on all 3 new codes).
"""
import uuid

import pytest

from app.core import scopes as ss12_scopes
from app.models.user import User, UserRole
from app.services.auth_service import create_access_token, hash_password


@pytest.fixture(autouse=True)
def _clean_ss12_state():
    ss12_scopes.reset_auth_counters()
    ss12_scopes.reset_rate_limits()
    yield
    ss12_scopes.reset_auth_counters()
    ss12_scopes.reset_rate_limits()


async def _login(client, db_session, role: str, prefix: str) -> dict:
    unique = uuid.uuid4().hex[:8]
    user = User(username=f"{prefix}_{unique}", email=f"{prefix}_{unique}@example.com",
                hashed_password=hash_password("testpass123"), role=role)
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


async def _create_key(client, headers, **overrides) -> dict:
    payload = {"name": "ss12-key", "preset": "full"}
    payload.update(overrides)
    resp = await client.post("/api/v1/api-keys", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_user(db_session, role: str, prefix: str) -> User:
    unique = uuid.uuid4().hex[:8]
    user = User(username=f"{prefix}_{unique}", email=f"{prefix}_{unique}@example.com",
                hashed_password=hash_password("testpass123"), role=role)
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


# --- create -----------------------------------------------------------------

class TestCreate:
    @pytest.mark.asyncio
    async def test_admin_create_201_bare_key_returned_exactly_once(
        self, client, db_session, admin_headers
    ):
        body = await _create_key(client, admin_headers)
        assert body["key"].startswith("cds_ak_")
        assert len(body["key"]) == 50
        assert body["status"] == "active"
        assert body["key_prefix"] == body["key"][:11]
        assert body["scopes"] and body["id"] and body["name"] == "ss12-key"
        assert "expires_at" in body and "created_at" in body

        # the bare key must never appear again — list has no key material
        resp = await client.get("/api/v1/api-keys", headers=admin_headers)
        assert resp.status_code == 200
        listing = resp.json()
        assert isinstance(listing, list) and len(listing) == 1
        row = listing[0]
        assert row["id"] == body["id"]
        assert row["key_prefix"] == body["key_prefix"]
        for field in ("key", "key_hash"):
            assert field not in row
        assert body["key"] not in resp.text

        # second create with the same name is allowed (keys are not unique by name)
        body2 = await _create_key(client, admin_headers, name="ss12-key-2")
        assert body2["key"] != body["key"]
        assert body2["id"] != body["id"]

    @pytest.mark.asyncio
    async def test_create_preset_expansion_and_custom(
        self, client, db_session, admin_headers
    ):
        for preset, expectation in [
            ("readonly", 12), ("operator", 19), ("full", 12),
        ]:
            body = await _create_key(client, admin_headers, preset=preset)
            assert len(body["scopes"]) == expectation

        custom = await _create_key(client, admin_headers, preset="custom",
                                   scopes=["meta:read", "sessions:*"])
        assert custom["scopes"] == ["meta:read", "sessions:*"]

        bad = await client.post("/api/v1/api-keys",
                                json={"name": "bad", "preset": "custom", "scopes": ["bogus:x"]},
                                headers=admin_headers)
        assert bad.status_code == 400

        none = await client.post("/api/v1/api-keys", json={"name": "none"}, headers=admin_headers)
        assert none.status_code == 422

    @pytest.mark.asyncio
    async def test_create_rate_limit_bounds(self, client, db_session, admin_headers):
        for bad_limit in (0, 1001):
            resp = await client.post("/api/v1/api-keys",
                                     json={"name": "rl", "preset": "full", "rate_limit": bad_limit},
                                     headers=admin_headers)
            assert resp.status_code == 422


# --- role & subject gates ----------------------------------------------------

class TestGates:
    @pytest.mark.asyncio
    async def test_operator_forbidden_on_admin_endpoints(self, client, db_session):
        headers = await _login(client, db_session, UserRole.OPERATOR, "op")
        resp = await client.post("/api/v1/api-keys", json={"name": "x", "preset": "full"},
                                 headers=headers)
        assert resp.status_code == 403
        resp = await client.get("/api/v1/api-keys", headers=headers)
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_key_subject_rejected_on_human_only_endpoints(
        self, client, db_session, admin_headers
    ):
        body = await _create_key(client, admin_headers)
        key_headers = {"Authorization": f"Bearer {body['key']}"}
        for method, path in [("GET", "/api/v1/api-keys"), ("POST", "/api/v1/api-keys"),
                             ("GET", "/api/v1/users/options"), ("POST", "/api/v1/auth/login")]:
            payload = {"name": "x", "preset": "full"} if method == "POST" and path == "/api/v1/api-keys" else None
            resp = await client.request(method, path, json=payload, headers=key_headers)
            # spec 7: 401/403 (human-only) — 401 via the get_current_user chain
            # (endpoint-level dependency), 403 via the router-level require_human
            assert resp.status_code in (401, 403), (method, path, resp.text)
            if resp.status_code == 403:
                assert resp.json()["code"] == "API_KEY_SCOPE_DENIED"


# --- scope gate on group endpoints ---------------------------------------------

class TestScopeGate:
    @pytest.mark.asyncio
    async def test_out_of_scope_get_is_403(self, client, db_session, admin_headers):
        body = await _create_key(client, admin_headers, preset="custom", scopes=["meta:read"])
        headers = {"Authorization": f"Bearer {body['key']}"}
        resp = await client.get("/api/v1/contracts", headers=headers)
        assert resp.status_code == 403
        payload = resp.json()
        assert payload["code"] == "API_KEY_SCOPE_DENIED"
        assert payload["detail"]["group"] == "contracts"
        assert payload["detail"]["verb"] == "read"

    @pytest.mark.asyncio
    async def test_write_verb_denied_for_read_scope(self, client, db_session, admin_headers):
        body = await _create_key(client, admin_headers, preset="custom", scopes=["contracts:read"])
        headers = {"Authorization": f"Bearer {body['key']}"}
        resp = await client.post("/api/v1/contracts", json={}, headers=headers)
        assert resp.status_code == 403
        assert resp.json()["detail"]["verb"] == "write"

    @pytest.mark.asyncio
    async def test_in_scope_get_passes_gate(self, client, db_session, admin_headers):
        body = await _create_key(client, admin_headers, preset="custom", scopes=["meta:read"])
        headers = {"Authorization": f"Bearer {body['key']}"}
        # D-e2e-09: gate passes AND the business surface resolves the issuer
        # identity → 200 (previously the call 401'd at the user-only dep,
        # making in-scope machine calls a dead end).
        resp = await client.get("/api/v1/meta/capability-matrix", headers=headers)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_star_scope_covers_verb(self, client, db_session, admin_headers):
        body = await _create_key(client, admin_headers, preset="custom", scopes=["contracts:*"])
        headers = {"Authorization": f"Bearer {body['key']}"}
        resp = await client.post("/api/v1/contracts", json={}, headers=headers)
        assert resp.status_code != 403  # gate passed; body validation may 4xx further


# --- rotate --------------------------------------------------------------------

class TestRotate:
    @pytest.mark.asyncio
    async def test_rotate_semantics(self, client, db_session, admin_headers):
        old = await _create_key(client, admin_headers, name="rot-me", preset="custom",
                                scopes=["meta:read"])
        resp = await client.post(f"/api/v1/api-keys/{old['id']}/rotate", headers=admin_headers)
        assert resp.status_code == 201
        new = resp.json()
        assert new["key"].startswith("cds_ak_") and len(new["key"]) == 50
        assert new["id"] != old["id"]

        listing = (await client.get("/api/v1/api-keys", headers=admin_headers)).json()
        by_id = {row["id"]: row for row in listing}
        assert by_id[old["id"]]["status"] == "revoked"
        assert by_id[new["id"]]["status"] == "active"
        assert by_id[new["id"]]["name"] == "rot-me"
        assert by_id[new["id"]]["scopes"] == old["scopes"]
        assert by_id[new["id"]]["rate_limit"] == by_id[old["id"]]["rate_limit"]

        # old key is dead, new key is live (gate + issuer identity → 200)
        old_resp = await client.get("/api/v1/meta/capability-matrix",
                                    headers={"Authorization": f"Bearer {old['key']}"})
        assert old_resp.status_code == 401
        assert old_resp.json()["code"] == "API_KEY_INACTIVE"
        assert old_resp.json()["detail"]["reason"] == "revoked"
        new_resp = await client.get("/api/v1/meta/capability-matrix",
                                    headers={"Authorization": f"Bearer {new['key']}"})
        assert new_resp.status_code == 200  # D-e2e-09: live key → issuer identity

        # rotating an already-revoked key → 400 already revoked
        again = await client.post(f"/api/v1/api-keys/{old['id']}/rotate", headers=admin_headers)
        assert again.status_code == 400
        assert "revoked" in str(again.json()["detail"])

    @pytest.mark.asyncio
    async def test_rotate_404_unknown_key(self, client, db_session, admin_headers):
        resp = await client.post(f"/api/v1/api-keys/{uuid.uuid4()}/rotate", headers=admin_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_rotate_expired_key_is_rejected(self, client, db_session, admin_headers):
        """M2 (CR 2026-10-15): rotating an expired key dropped its past
        deadline and issued a credential that never expires."""
        from datetime import datetime, timedelta, timezone

        from app.models.api_key import ApiKey

        old = await _create_key(client, admin_headers, name="gone-dead", expires_in_days=30)
        row = await db_session.get(ApiKey, uuid.UUID(old["id"]))
        row.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        await db_session.flush()

        resp = await client.post(f"/api/v1/api-keys/{old['id']}/rotate", headers=admin_headers)
        assert resp.status_code == 400, resp.text
        assert resp.json()["code"] == "BAD_REQUEST"
        assert resp.json()["detail"]["reason"] == "expired"

    @pytest.mark.asyncio
    async def test_rotate_carries_deadline_and_description(self, client, db_session, admin_headers):
        from app.models.api_key import ApiKey

        old = await _create_key(client, admin_headers, name="keep-me", expires_in_days=90)
        row = await db_session.get(ApiKey, uuid.UUID(old["id"]))
        row.description = "billing integration"
        await db_session.flush()

        new = await client.post(f"/api/v1/api-keys/{old['id']}/rotate", headers=admin_headers)
        assert new.status_code == 201, new.text
        new_row = await db_session.get(ApiKey, uuid.UUID(new.json()["id"]))
        old_row = await db_session.get(ApiKey, uuid.UUID(old["id"]))
        assert new_row.description == "billing integration"
        assert new_row.expires_at is not None
        assert new_row.expires_at == old_row.expires_at


# --- patch / delete ---------------------------------------------------------------

class TestPatchDelete:
    @pytest.mark.asyncio
    async def test_patch_disabled_recoverable_revoked_terminal(self, client, db_session, admin_headers):
        body = await _create_key(client, admin_headers)
        resp = await client.patch(f"/api/v1/api-keys/{body['id']}", json={"status": "disabled"},
                                  headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "disabled"

        resp = await client.patch(f"/api/v1/api-keys/{body['id']}", json={"status": "active"},
                                  headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

        del1 = await client.delete(f"/api/v1/api-keys/{body['id']}", headers=admin_headers)
        assert del1.status_code == 200
        resp = await client.patch(f"/api/v1/api-keys/{body['id']}", json={"status": "active"},
                                  headers=admin_headers)
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_delete_idempotent(self, client, db_session, admin_headers):
        body = await _create_key(client, admin_headers)
        first = await client.delete(f"/api/v1/api-keys/{body['id']}", headers=admin_headers)
        assert first.status_code == 200
        first = first.json()
        assert first["status"] == "revoked" and first["id"] == body["id"]
        second = await client.delete(f"/api/v1/api-keys/{body['id']}", headers=admin_headers)
        assert second.status_code == 200
        second = second.json()
        assert second["id"] == first["id"] and second["status"] == "revoked"
        # same instant (tz formatting differs between fresh and re-read rows)
        assert second["revoked_at"][:19] == first["revoked_at"][:19]


# --- W2 error contract ----------------------------------------------------------------

class TestW2Errors:
    @pytest.mark.asyncio
    async def test_all_three_new_codes_carry_full_w2_body(self, client, db_session, admin_headers):
        w2_fields = {"code", "message", "detail", "request_id"}

        bad = await client.get("/api/v1/meta/capability-matrix",
                               headers={"Authorization": "Bearer cds_ak_" + "z" * 43})
        assert bad.status_code == 401
        assert bad.json()["code"] == "API_KEY_INVALID"
        assert w2_fields.issubset(bad.json().keys())

        body = await _create_key(client, admin_headers)
        await client.patch(f"/api/v1/api-keys/{body['id']}", json={"status": "disabled"},
                           headers=admin_headers)
        disabled = await client.get("/api/v1/meta/capability-matrix",
                                    headers={"Authorization": f"Bearer {body['key']}"})
        assert disabled.status_code == 401
        assert disabled.json()["code"] == "API_KEY_INACTIVE"
        assert disabled.json()["detail"]["reason"] == "disabled"
        assert w2_fields.issubset(disabled.json().keys())

        ro = await _create_key(client, admin_headers, preset="custom", scopes=["meta:read"])
        denied = await client.get("/api/v1/contracts",
                                  headers={"Authorization": f"Bearer {ro['key']}"})
        assert denied.status_code == 403
        assert denied.json()["code"] == "API_KEY_SCOPE_DENIED"
        assert w2_fields.issubset(denied.json().keys())

    @pytest.mark.asyncio
    async def test_rate_limit_429_with_retry_after(self, client, db_session, admin_headers):
        body = await _create_key(client, admin_headers, preset="custom",
                                 scopes=["meta:read"], rate_limit=1)
        headers = {"Authorization": f"Bearer {body['key']}"}
        first = await client.get("/api/v1/meta/capability-matrix", headers=headers)
        assert first.status_code == 200  # gate passed, issuer identity (D-e2e-09)
        second = await client.get("/api/v1/meta/capability-matrix", headers=headers)
        assert second.status_code == 429
        assert second.json()["code"] == "RATE_LIMITED"
        assert int(second.headers["Retry-After"]) >= 1
