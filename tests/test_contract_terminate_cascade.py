"""Contract termination cascade tests (gap A1).

Terminating a contract must cascade-reclaim its sandbox sessions (container/
key/network-policy/quota via the lifecycle primitive) and revoke its policy
bundles (DB + OPA), plus auto-terminate ACTIVE contracts past valid_until.
"""
import uuid
from datetime import datetime, timezone, timedelta

import pytest

from app.models.contract import Contract, ContractStatus
from app.models.data_product import DataProduct
from app.models.sandbox_session import SandboxSession, SessionStatus
from app.models.policy_bundle import PolicyBundle


async def _mk_contract(db_session, provider_id, buyer_id, *, status=ContractStatus.ACTIVE.value, valid_until=None, max_output_rows=10):
    contract = Contract(
        contract_no=f"C-{uuid.uuid4().hex[:8]}",
        contract_type="data_query",
        provider_id=uuid.UUID(provider_id),
        buyer_id=uuid.UUID(buyer_id),
        title="cascade-test",
        product_ids=[str(uuid.uuid4())],
        status=status,
        valid_until=valid_until,
        max_output_rows=max_output_rows,
    )
    db_session.add(contract)
    await db_session.flush()
    await db_session.refresh(contract)
    return contract


@pytest.mark.asyncio
async def test_terminate_cascades_sessions_and_revokes_bundles(db_session, make_user):
    from app.services.contract_service import contract_service

    _, provider = await make_user("data_provider", "prov")
    _, buyer = await make_user("buyer", "buy")

    contract = await _mk_contract(db_session, provider, buyer)

    product = DataProduct(provider_id=uuid.UUID(provider), name="p", status="published")
    db_session.add(product)
    await db_session.flush()
    await db_session.refresh(product)

    session = SandboxSession(
        user_id=uuid.UUID(buyer),
        data_product_id=product.id,
        sandbox_level="L3",
        status=SessionStatus.RUNNING.value,
        contract_id=str(contract.id),
        timeout_seconds=3600,
    )
    db_session.add(session)
    bundle = PolicyBundle(
        policy_id=f"cds/policies/contract_{contract.id}",
        contract_id=contract.id,
        rego_source="package cds",
        integrity_hash="sm3-hash",
    )
    db_session.add(bundle)
    await db_session.flush()

    result = await contract_service.terminate(db_session, contract.id, uuid.UUID(provider))
    await db_session.flush()

    assert result.status == ContractStatus.TERMINATED.value
    assert session.status == SessionStatus.TERMINATED.value
    await db_session.refresh(bundle)
    assert bundle.revoked_at is not None


@pytest.mark.asyncio
async def test_terminate_idempotent_without_sessions_or_bundles(db_session, make_user):
    from app.services.contract_service import contract_service

    _, provider = await make_user("data_provider", "prov")
    _, buyer = await make_user("buyer", "buy")
    contract = await _mk_contract(db_session, provider, buyer)
    await db_session.flush()

    result = await contract_service.terminate(db_session, contract.id, uuid.UUID(provider))
    await db_session.flush()
    assert result.status == ContractStatus.TERMINATED.value


@pytest.mark.asyncio
async def test_terminate_requires_party(db_session, make_user):
    from app.services.contract_service import contract_service

    _, provider = await make_user("data_provider", "prov")
    _, buyer = await make_user("buyer", "buy")
    _, stranger = await make_user("buyer", "stranger")
    contract = await _mk_contract(db_session, provider, buyer)
    await db_session.flush()

    with pytest.raises(ValueError):
        await contract_service.terminate(db_session, contract.id, uuid.UUID(stranger))


@pytest.mark.asyncio
async def test_terminate_expired_contracts_sweep(db_session, make_user):
    from app.services.contract_service import contract_service

    _, provider = await make_user("data_provider", "prov")
    _, buyer = await make_user("buyer", "buy")

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    expired = await _mk_contract(db_session, provider, buyer, valid_until=past)
    future = await _mk_contract(
        db_session, provider, buyer,
        valid_until=datetime.now(timezone.utc) + timedelta(days=30),
    )
    await db_session.flush()

    count = await contract_service.terminate_expired_contracts(db_session)
    assert count == 1
    assert expired.status == ContractStatus.TERMINATED.value
    assert future.status == ContractStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_activation_sets_valid_until_from_terms(db_session, make_user):
    """Signing a contract whose terms carry valid_until populates the column
    so the expiry sweep can later auto-terminate it."""
    from app.services.contract_service import contract_service

    _, provider = await make_user("data_provider", "prov")
    _, buyer = await make_user("buyer", "buy")
    contract = await _mk_contract(db_session, provider, buyer, status=ContractStatus.NEGOTIATING.value)
    contract.terms = {"valid_until": (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()}
    await db_session.flush()

    # Simulate the activation-time population step (as in the sign handler)
    contract_service._apply_valid_until_from_terms(contract)
    assert contract.valid_until is not None
