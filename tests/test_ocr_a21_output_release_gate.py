"""A21 — the output gateway must be bound to a session (fail-closed + audited).

``POST /api/v1/output-control/gateway`` took ``session_id`` as *optional*:

1. ``_authorize_output_session`` returned early on a missing ``session_id`` — no
   ownership check at all — and the handler also skipped
   ``session_contract_id()``, so ``build_output_policy(db, None)`` handed back the
   *platform default* policy. Gap E1 ("the request may only narrow the contract's
   allowance, never widen it") was therefore escapable by deleting one field: a
   session governed by a 2-row/CSV-only contract released 10 000 rows as JSON.
2. ``_authorize_owned_session_id(..., create_if_missing=True)`` was the default,
   and a UUID-shaped id with no session row fell through to
   ``owner_store[session_id] = current_user.id`` — an authorization failure was
   converted into a grant of ownership (whoever guesses a session id first owns
   it, and the real owner is then locked out with 403).
3. The two owner tables were unbounded module-level dicts — any authenticated
   user could grow them without limit by calling ``/inspect`` with fresh ids.

Every refusal below is written with ``audit_service.log_standalone`` because a
raised HTTPException rolls the request transaction back (an in-transaction
``audit_service.log`` before a raise would be swallowed).
"""
import uuid

import pytest

from app.api import output_control as oc
from app.models.contract import Contract, ContractStatus
from app.models.data_product import DataProduct, DataProductStatus
from app.models.sandbox_session import SandboxSession, SessionStatus
from app.models.user import User
from app.services.audit_service import audit_service
from app.services.auth_service import create_access_token


async def _headers(db_session, user_id: str) -> dict:
    user = await db_session.get(User, uuid.UUID(user_id))
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


async def _session_with_contract(db_session, make_user, *, max_rows=2, formats="csv", epsilon=None):
    """A provider session governed by a strict output contract."""
    _, provider_id = await make_user("data_provider", "a21_provider")
    _, buyer_id = await make_user("buyer", "a21_buyer")
    product = DataProduct(
        provider_id=uuid.UUID(provider_id), name="A21 Product",
        product_type="structured", status=DataProductStatus.PUBLISHED.value,
    )
    db_session.add(product)
    await db_session.flush()
    contract = Contract(
        contract_no=f"A21-{uuid.uuid4().hex[:12]}",
        contract_type="data_query",
        status=ContractStatus.ACTIVE.value,
        provider_id=uuid.UUID(provider_id),
        buyer_id=uuid.UUID(buyer_id),
        title="A21 Contract",
        product_ids=[str(product.id)],
        max_output_rows=max_rows,
        allowed_output_formats=formats,
        dp_epsilon_budget=epsilon,
    )
    db_session.add(contract)
    await db_session.flush()
    session = SandboxSession(
        user_id=uuid.UUID(provider_id),
        data_product_id=product.id,
        contract_id=str(contract.id),
        status=SessionStatus.RUNNING.value,
        container_id="a21-container",
        timeout_seconds=3600,
    )
    db_session.add(session)
    await db_session.flush()
    await db_session.refresh(session)
    return provider_id, str(session.id), str(contract.id)


@pytest.fixture
def audit_spy(monkeypatch):
    """Record ``log_standalone`` calls (refusals must survive the rollback)."""
    calls: list[dict] = []

    async def _record(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(audit_service, "log_standalone", _record)
    return calls


@pytest.mark.asyncio
async def test_gateway_without_session_id_is_refused_and_audited(
    client, db_session, make_user, audit_spy
):
    """Dropping session_id must no longer buy the platform default allowance."""
    provider_id, _session_id, _contract_id = await _session_with_contract(db_session, make_user)
    headers = await _headers(db_session, provider_id)

    resp = await client.post(
        "/api/v1/output-control/gateway",
        json={"data": [{"x": i} for i in range(5)], "output_format": "json",
              "max_output_rows": 9999, "allowed_output_formats": ["json", "csv"]},
        headers=headers,
    )

    assert resp.status_code == 403, (
        "an unbound release must be refused, not served with default caps: "
        f"got {resp.status_code} -> {resp.json()}"
    )
    assert resp.json()["code"] == "HTTP_403"
    assert audit_spy, "a refused data release must leave an audit trail"
    actions = [c.get("action") for c in audit_spy]
    assert any("gateway" in (a or "") and "denied" in (a or "") for a in actions), actions
    entry = audit_spy[-1]
    assert str(entry.get("user_id")) == provider_id
    assert entry.get("detail", {}).get("reason")


@pytest.mark.asyncio
async def test_gateway_rejects_an_ad_hoc_session_id(client, db_session, make_user):
    """"Guess any string" is not a session: no claim, no policy, no release."""
    provider_id, _sid, _cid = await _session_with_contract(db_session, make_user)
    headers = await _headers(db_session, provider_id)

    resp = await client.post(
        "/api/v1/output-control/gateway",
        json={"data": [{"x": 1}], "session_id": f"adhoc-{uuid.uuid4().hex[:8]}"},
        headers=headers,
    )

    assert resp.status_code == 404, resp.text
    assert str(uuid.uuid4())  # keep uuid import used in failure messages
    assert oc._output_session_owners.get("unused") is None


@pytest.mark.asyncio
async def test_gateway_rejects_a_nonexistent_uuid_session(client, db_session, make_user):
    """A UUID-shaped id with no session row cannot be claimed as one's own."""
    provider_id, _sid, _cid = await _session_with_contract(db_session, make_user)
    headers = await _headers(db_session, provider_id)
    ghost = str(uuid.uuid4())

    resp = await client.post(
        "/api/v1/output-control/gateway",
        json={"data": [{"x": 1}], "session_id": ghost},
        headers=headers,
    )

    assert resp.status_code == 404
    assert ghost not in oc._output_session_owners, "the claim path must not mint ownership"


@pytest.mark.asyncio
async def test_gateway_rejects_another_users_session(client, db_session, make_user):
    """The authorization boundary itself stays where it was (403, not loosened)."""
    _provider_id, session_id, _cid = await _session_with_contract(db_session, make_user)
    _other, stranger = await make_user("data_provider", "a21_stranger")
    headers = await _headers(db_session, stranger)

    resp = await client.post(
        "/api/v1/output-control/gateway",
        json={"data": [{"x": 1}], "session_id": session_id},
        headers=headers,
    )

    assert resp.status_code == 403
    assert resp.json()["code"] == "HTTP_403"


@pytest.mark.asyncio
async def test_bound_session_still_narrows_to_the_contract(client, db_session, make_user):
    """Reverse case: the legitimate release path works and stays contract-bounded."""
    provider_id, session_id, contract_id = await _session_with_contract(db_session, make_user)
    headers = await _headers(db_session, provider_id)

    resp = await client.post(
        "/api/v1/output-control/gateway",
        json={"data": [{"x": i} for i in range(5)], "session_id": session_id,
              "output_format": "csv", "max_output_rows": 9999,
              "allowed_output_formats": ["csv", "json"]},
        headers=headers,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["row_count"] == 2, "the 2-row contract cap must apply"
    assert body["truncated"] is True


@pytest.mark.asyncio
async def test_inspect_rejects_a_nonexistent_uuid_session(client, db_session, make_user):
    """/inspect keeps ad-hoc ids but must not mint ownership for UUID-shaped ones."""
    provider_id, _sid, _cid = await _session_with_contract(db_session, make_user)
    headers = await _headers(db_session, provider_id)
    ghost = str(uuid.uuid4())

    resp = await client.post(
        "/api/v1/output-control/inspect",
        json={"output": "safe text", "session_id": ghost},
        headers=headers,
    )

    assert resp.status_code == 404
    assert ghost not in oc._output_session_owners


@pytest.mark.asyncio
async def test_inspect_ad_hoc_session_behaviour_unchanged(client, db_session, make_user):
    """Reverse case: the pinned ad-hoc flow (first caller owns, others 403)."""
    owner_id, _sid, _cid = await _session_with_contract(db_session, make_user)
    _other, stranger = await make_user("data_provider", "a21_adhoc_stranger")
    session_id = f"adhoc-{uuid.uuid4().hex[:8]}"

    first = await client.post("/api/v1/output-control/inspect",
                              json={"output": "owner text", "session_id": session_id},
                              headers=await _headers(db_session, owner_id))
    assert first.status_code == 200, first.text

    again = await client.post("/api/v1/output-control/inspect",
                              json={"output": "owner text again", "session_id": session_id},
                              headers=await _headers(db_session, owner_id))
    assert again.status_code == 200, again.text

    other = await client.post("/api/v1/output-control/inspect",
                              json={"output": "other text", "session_id": session_id},
                              headers=await _headers(db_session, stranger))
    assert other.status_code == 403
    assert other.json()["code"] == "HTTP_403"


def test_owner_claim_store_is_bounded():
    """Unbounded claim dicts are a memory-exhaustion vector (A21 item 3)."""
    cap = getattr(oc, "_OWNER_CLAIM_CAP", None)
    assert isinstance(cap, int) and cap > 0, "claims must be kept bounded by a cap"

    store: dict[str, str] = {}
    for i in range(cap + 100):
        oc._claim_owner(store, f"sess-{i}", "user-1")

    assert len(store) <= cap
    assert store.get(f"sess-{cap + 99}") == "user-1", "the newest claim must be kept"


@pytest.mark.asyncio
async def test_dp_budget_ad_hoc_flow_still_works(client, db_session, make_user):
    """Reverse case: DP budget init/read for an ad-hoc id stays usable, and a
    UUID-shaped id that is not a session is still refused."""
    provider_id, _sid, _cid = await _session_with_contract(db_session, make_user)
    headers = await _headers(db_session, provider_id)
    session_id = f"budget-{uuid.uuid4().hex[:8]}"

    init = await client.post(
        f"/api/v1/output-control/dp/budget/init?session_id={session_id}&epsilon=10.0",
        headers=headers)
    assert init.status_code == 200, init.text

    got = await client.get(f"/api/v1/output-control/dp/budget/{session_id}", headers=headers)
    assert got.status_code == 200, got.text
    assert got.json()["epsilon_allocated"] is None or "epsilon_remaining" in got.json()

    ghost = str(uuid.uuid4())
    refused = await client.post(
        f"/api/v1/output-control/dp/budget/init?session_id={ghost}&epsilon=10.0",
        headers=headers)
    assert refused.status_code == 404
    assert ghost not in oc._dp_budget_owners
