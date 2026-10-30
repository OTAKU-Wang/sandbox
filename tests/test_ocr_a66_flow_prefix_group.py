"""A66 — `/api/v1/flow` must be covered by a functional group (SS-12 默认拒绝).

`app/main.py` mounts the SS-15 flow router with
`dependencies=[Depends(require_group("sessions"))]`, but `GROUP_PREFIXES` had no
`/api/v1/flow` entry.  `resolve_group()` therefore returned None, so the raw-ASGI
`Ss12KeyDefaultDenyMiddleware` rejected EVERY machine key on the whole flow
surface with `detail.reason = "unmapped_prefix"` before routing — the mounted
scope dependency was unreachable code and SDK v0.3.0's flow operations were dead.

Two readings per level:
  * unit  — resolve_group()/key_unmapped_deny() for the flow prefix, plus a
            snapshot lock proving no OTHER group's prefixes were widened;
  * ASGI  — a real key request against GET /api/v1/flow/runtime-status: it must
            now reach the group gate (403 detail.group == "sessions" when the key
            lacks the scope) and must actually serve when the key has it.
"""
import uuid

import pytest

from app.core import scopes as ss12_scopes
from app.models.user import User, UserRole
from app.services.auth_service import create_access_token, hash_password

_KEY = "Bearer cds_ak_" + "z" * 43


@pytest.fixture(autouse=True)
def _clean_ss12_state():
    ss12_scopes.reset_auth_counters()
    ss12_scopes.reset_rate_limits()
    yield
    ss12_scopes.reset_auth_counters()
    ss12_scopes.reset_rate_limits()


# --- unit: the flow prefix is mapped ------------------------------------------------

def test_flow_paths_resolve_to_sessions_group():
    for path in ("/api/v1/flow", "/api/v1/flow/runtime-status",
                 "/api/v1/flow/dashboard/home"):
        assert ss12_scopes.resolve_group(path) == "sessions", (
            f"{path} must map to the sessions group that main.py mounts it under, "
            f"got {ss12_scopes.resolve_group(path)!r}"
        )


def test_flow_key_request_is_not_unmapped_deny():
    assert ss12_scopes.key_unmapped_deny("/api/v1/flow/runtime-status", _KEY) is False


def test_flow_group_is_reachable_by_scope_check():
    assert "sessions" in ss12_scopes.REACHABLE_GROUPS
    assert ss12_scopes.check_scope(["sessions:read"], "sessions", "read") is True


# --- unit lock: nothing else moved --------------------------------------------------

# One representative path per group, exactly as mounted in app/main.py.  If a future
# edit widens a prefix (or reorders longest-prefix matching) this snapshot reddens —
# A66's fix must add the flow prefix and nothing else.
_GROUP_SNAPSHOT = {
    "/api/v1/sandbox-sessions/s-1": "sessions",
    "/api/v1/dev-sandbox/x": "sessions",
    "/api/v1/notebooks/n-1": "sessions",
    "/api/v1/workloads/w-1": "sessions",
    "/api/v1/flow/runtime-status": "sessions",
    "/api/v1/contracts/c-1": "contracts",
    "/api/v1/data-products/p-1": "data",
    "/api/v1/data-resources/r-1": "data",
    "/api/v1/catalog": "data",
    "/api/v1/data-pipeline/x": "pipeline",
    "/api/v1/training/j-1": "pipeline",
    "/api/v1/mpc/x": "pipeline",
    "/api/v1/rag/x": "pipeline",
    "/api/v1/inference/x": "pipeline",
    "/api/v1/sandbox-db/x": "pipeline",
    "/api/v1/sandbox-tasks/t-1": "pipeline",
    "/api/v1/compute/x": "pipeline",
    "/api/v1/audit/logs": "audit",
    "/api/v1/monitoring/x": "audit",
    "/api/v1/compliance/x": "audit",
    "/metrics": "audit",
    "/health": "audit",
    "/api/v1/output-control/x": "output",
    "/api/v1/field-exposure/x": "output",
    "/api/v1/network-policies/x": "output",
    "/api/v1/sandbox-nodes/n": "infra",
    "/api/v1/shared-volumes/v": "infra",
    "/api/v1/cube-templates/t": "infra",
    "/api/v1/kms/x": "infra",
    "/api/v1/connectors/c": "infra",
    "/api/v1/ops/x": "infra",
    "/api/v1/certificates/c": "certs",
    "/api/v1/federation/spaces": "federation",
    "/api/v1/gateway/x": "gateway",
    "/api/v1/deliveries/d": "gateway",
    "/open/v1/x": "gateway",
    "/api/v1/agent/x": "agent",
    "/api/v1/meta/capability-matrix": "meta",
}

# human-only surfaces have NO group at all (spec 4.2) — must stay unmapped
_HUMAN_ONLY = ("/api/v1/auth/login", "/api/v1/users/u", "/api/v1/api-keys/k",
               "/internal/audit/ingest", "/static/admin.js")

# and a genuinely unmapped prefix must STAY default-denied (fail-closed).
# NOTE: prefix matching in resolve_group is boundary-loose (startswith(prefix)),
# so only a prefix with no group-shaped stem can serve as the unmapped witness.
_STILL_UNMAPPED = ("/api/v1/not-a-real-surface", "/open/v2/x")


def test_group_snapshot_unchanged_except_flow():
    for path, group in _GROUP_SNAPSHOT.items():
        assert ss12_scopes.resolve_group(path) == group, f"group drift on {path}"


def test_human_only_still_unmapped():
    for path in _HUMAN_ONLY:
        assert ss12_scopes.resolve_group(path) is None, f"{path} must stay human-only"


def test_unmapped_prefix_still_default_denied():
    for path in _STILL_UNMAPPED:
        assert ss12_scopes.resolve_group(path) is None, f"{path} must stay unmapped"
        assert ss12_scopes.key_unmapped_deny(path, _KEY) is True


# --- ASGI: the mounted scope gate is actually reachable -----------------------------

async def _login(client, db_session, role: str, prefix: str) -> dict[str, str]:
    unique = uuid.uuid4().hex[:8]
    user = User(username=f"{prefix}_{unique}", email=f"{prefix}_{unique}@example.com",
                hashed_password=hash_password("testpass123"), role=role)
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


async def _create_key(client, headers, **overrides) -> dict[str, str]:
    payload = {"name": "a66-key", "preset": "custom", "scopes": ["meta:read"]}
    payload.update(overrides)
    resp = await client.post("/api/v1/api-keys", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_flow_denial_now_names_the_group_not_unmapped_prefix(
    client, db_session, admin_headers
):
    key = await _create_key(client, admin_headers, preset="custom", scopes=["meta:read"])
    resp = await client.get("/api/v1/flow/runtime-status",
                            headers={"Authorization": f"Bearer {key['key']}"})
    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert body["code"] == "API_KEY_SCOPE_DENIED"
    assert body["detail"].get("reason") != "unmapped_prefix", (
        "flow surface is still default-denied as an unmapped prefix — "
        f"detail={body['detail']}"
    )
    assert body["detail"]["group"] == "sessions"
    assert body["detail"]["verb"] == "read"


@pytest.mark.asyncio
async def test_in_scope_key_serves_flow_surface(client, db_session, admin_headers):
    key = await _create_key(client, admin_headers, preset="custom", scopes=["sessions:read"])
    resp = await client.get("/api/v1/flow/runtime-status",
                            headers={"Authorization": f"Bearer {key['key']}"})
    assert resp.status_code == 200, resp.text
    assert "available" in resp.json()
