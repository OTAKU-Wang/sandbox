"""A19 — contract activation must take the row lock before it mutates state.

``POST /api/v1/contracts/{id}/activate`` read the contract with a plain
``SELECT`` (``contract_service.get_by_id``), tested ``status == "signed"`` and
then wrote ``status = "active"``, allocated the DP epsilon budget and ran
auto-fulfillment. Two concurrent activations (double click, client retry, a
second API replica) both read ``signed``, so the privacy budget was allocated
twice and the contract was fulfilled twice (two sets of provisioned sessions).

The fix is a locking read (``SELECT ... FOR UPDATE``) around the state
transition. SQLite — the local/CI harness engine — silently drops ``FOR UPDATE``
from the emitted SQL, so the locking read is asserted by compiling the captured
statement with the PostgreSQL dialect: the deployment target where the lock is
what makes the second activation block and then observe ``active``.
"""
import uuid

import pytest
from sqlalchemy import Select, select
from sqlalchemy.dialects import postgresql

import app.api.contracts as contracts_api
from app.models.contract import Contract, ContractStatus
from app.models.data_product import DataProduct, DataProductStatus
from app.services.auth_service import create_access_token


def _contract_selects(statements) -> list[tuple[str, bool]]:
    """``[(postgresql_sql, carries_row_lock), ...]`` over the ``contracts`` table."""
    found = []
    for stmt in statements:
        if not isinstance(stmt, Select):
            continue
        try:
            froms = [str(f) for f in stmt.get_final_froms()]
        except Exception:  # pragma: no cover - non-Table FROMs
            continue
        if "contracts" not in froms:
            continue
        found.append(
            (str(stmt.compile(dialect=postgresql.dialect())),
             "FOR UPDATE" in str(stmt.compile(dialect=postgresql.dialect())).upper())
        )
    return found


async def _signed_contract(db_session, make_user, *, epsilon: float = 1.0):
    _, provider_id = await make_user("data_provider", "a19_provider")
    _, buyer_id = await make_user("buyer", "a19_buyer")
    product = DataProduct(
        provider_id=uuid.UUID(provider_id),
        name="A19 Product",
        product_type="structured",
        status=DataProductStatus.PUBLISHED.value,
    )
    db_session.add(product)
    await db_session.flush()
    contract = Contract(
        contract_no=f"A19-{uuid.uuid4().hex[:12]}",
        contract_type="data_query",
        status=ContractStatus.SIGNED.value,
        provider_id=uuid.UUID(provider_id),
        buyer_id=uuid.UUID(buyer_id),
        title="A19 Contract",
        product_ids=[str(product.id)],
        dp_epsilon_budget=epsilon,
        terms={"output_policy": {"max_output_rows": 100, "allowed_formats": ["csv"]}},
    )
    db_session.add(contract)
    await db_session.flush()
    await db_session.refresh(contract)
    return provider_id, str(contract.id)


async def _headers_for(db_session, user_id: str) -> dict:
    from app.models.user import User

    user = await db_session.get(User, uuid.UUID(user_id))
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


def _spies(monkeypatch, db_session):
    """Capture statements, allocations and fulfillments of one activation."""
    captured, fulfilled, allocated = [], [], []
    real_execute = db_session.execute

    async def spy_execute(statement, *args, **kwargs):
        captured.append(statement)
        return await real_execute(statement, *args, **kwargs)

    real_allocate = contracts_api.dp_budget_ledger.allocate

    async def spy_allocate(db, contract_id, eps):
        allocated.append((str(contract_id), float(eps)))
        return await real_allocate(db, contract_id, eps)

    async def fake_fulfill(db, contract_id):
        fulfilled.append(str(contract_id))
        return {"sessions": 1}

    monkeypatch.setattr(db_session, "execute", spy_execute)
    monkeypatch.setattr(contracts_api.dp_budget_ledger, "allocate", spy_allocate)
    monkeypatch.setattr(contracts_api.contract_fulfillment, "fulfill_contract", fake_fulfill)
    return captured, fulfilled, allocated


@pytest.mark.asyncio
async def test_activation_reads_the_contract_row_with_a_lock(
    client, db_session, make_user, monkeypatch
):
    provider_id, contract_id = await _signed_contract(db_session, make_user)
    captured, fulfilled, allocated = _spies(monkeypatch, db_session)
    headers = await _headers_for(db_session, provider_id)

    resp = await client.post(f"/api/v1/contracts/{contract_id}/activate", headers=headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"
    assert fulfilled == [contract_id]
    assert allocated == [(contract_id, 1.0)]

    selects = _contract_selects(captured)
    assert selects, "no SELECT over `contracts` was captured — the spy is blind"
    assert any(locked for _sql, locked in selects), (
        "activation must read the contract row FOR UPDATE: concurrent "
        "activations otherwise both observe `signed` and double-allocate the "
        "DP budget + double-fulfil (A19). Captured: "
        + " | ".join(sql[:140] for sql, _ in selects)
    )


@pytest.mark.asyncio
async def test_activating_an_active_contract_is_idempotent(
    client, db_session, make_user, monkeypatch
):
    """Reverse case: the legitimate path stays usable and runs its side effects once."""
    provider_id, contract_id = await _signed_contract(db_session, make_user)
    _captured, fulfilled, allocated = _spies(monkeypatch, db_session)
    headers = await _headers_for(db_session, provider_id)

    first = await client.post(f"/api/v1/contracts/{contract_id}/activate", headers=headers)
    assert first.status_code == 200, first.text
    second = await client.post(f"/api/v1/contracts/{contract_id}/activate", headers=headers)

    assert second.status_code == 200, second.text
    assert second.json()["status"] == "active"
    assert fulfilled == [contract_id], "an already-active contract must not be fulfilled again"
    assert allocated == [(contract_id, 1.0)]


@pytest.mark.asyncio
async def test_non_party_activation_still_403(client, db_session, make_user):
    """The tightening must not move the existing authorization boundary."""
    _provider_id, contract_id = await _signed_contract(db_session, make_user)
    outsider_headers, _ = await make_user("buyer", "a19_outsider")

    resp = await client.post(f"/api/v1/contracts/{contract_id}/activate", headers=outsider_headers)

    assert resp.status_code == 403
    assert resp.json()["code"] == "HTTP_403"
