import uuid

import pytest
from sqlalchemy import select

from app.models.contract import Contract, ContractStatus, ContractType
from app.models.data_product import DataProduct, DataProductStatus
from app.models.kms import KeyMetadata
from app.models.sandbox_session import SandboxSession, SessionStatus
from app.services.contract_fulfillment import contract_fulfillment


class FailedFulfillmentRuntime:
    def provision(self, **kwargs):
        return {"container_id": None, "status": "failed", "error": "runtime provision failed"}

    def terminate(self, container_id: str):
        return True


class SuccessfulFulfillmentRuntime:
    def __init__(self):
        self.terminated = []

    def provision(self, **kwargs):
        return {"container_id": "fulfillment-container", "status": "running"}

    def terminate(self, container_id: str):
        self.terminated.append(container_id)
        return True


class FailingFulfillmentKMS:
    def __init__(self):
        self.destroyed = []

    def generate_session_key(self, session_id: str):
        return {"key_id": f"session-{session_id}-fail", "key_bytes": b"0" * 32}

    def distribute_key(self, key_id: str, session_id: str, **kwargs):
        return None

    def destroy_key(self, key_id: str):
        self.destroyed.append(key_id)
        return True


async def _make_product(db_session, make_user):
    _, provider_id = await make_user("data_provider", "fulfill_provider")
    product = DataProduct(
        provider_id=uuid.UUID(provider_id),
        name="Fulfillment Product",
        product_type="structured",
        status=DataProductStatus.PUBLISHED.value,
    )
    db_session.add(product)
    await db_session.flush()
    return product, uuid.UUID(provider_id)


@pytest.mark.asyncio
async def test_contract_constraints_fail_closed_when_bound_contract_missing(db_session, make_user):
    product, _ = await _make_product(db_session, make_user)
    _, buyer_id = await make_user("buyer", "fulfill_buyer")
    session = SandboxSession(
        user_id=uuid.UUID(buyer_id),
        data_product_id=product.id,
        sandbox_level="L3",
        sandbox_mode="structured_query",
        contract_id=str(uuid.uuid4()),
        status=SessionStatus.RUNNING.value,
    )
    db_session.add(session)
    await db_session.flush()

    result = await contract_fulfillment.check_contract_constraints(db_session, session.id, "query")

    assert result["allowed"] is False
    assert result["reason"] == "Bound contract not found"


@pytest.mark.asyncio
async def test_contract_constraints_reject_disallowed_sandbox_mode(db_session, make_user):
    product, provider_id = await _make_product(db_session, make_user)
    _, buyer_id = await make_user("buyer", "fulfill_buyer")
    contract = Contract(
        contract_no=f"FUL-{uuid.uuid4().hex[:12]}",
        contract_type=ContractType.DATA_QUERY.value,
        status=ContractStatus.ACTIVE.value,
        provider_id=provider_id,
        buyer_id=uuid.UUID(buyer_id),
        title="Fulfillment Contract",
        terms={},
        product_ids=[str(product.id)],
        allowed_operations="query",
        allowed_sandbox_modes=["product_dev"],
    )
    db_session.add(contract)
    await db_session.flush()
    session = SandboxSession(
        user_id=uuid.UUID(buyer_id),
        data_product_id=product.id,
        sandbox_level="L3",
        sandbox_mode="structured_query",
        contract_id=str(contract.id),
        status=SessionStatus.RUNNING.value,
    )
    db_session.add(session)
    await db_session.flush()

    result = await contract_fulfillment.check_contract_constraints(db_session, session.id, "query")

    assert result["allowed"] is False
    assert "not in contract allowed_sandbox_modes" in result["reason"]


@pytest.mark.asyncio
async def test_contract_constraints_allow_active_matching_contract(db_session, make_user):
    product, provider_id = await _make_product(db_session, make_user)
    _, buyer_id = await make_user("buyer", "fulfill_buyer")
    contract = Contract(
        contract_no=f"FUL-{uuid.uuid4().hex[:12]}",
        contract_type=ContractType.DATA_QUERY.value,
        status=ContractStatus.ACTIVE.value,
        provider_id=provider_id,
        buyer_id=uuid.UUID(buyer_id),
        title="Fulfillment Contract",
        terms={},
        product_ids=[str(product.id)],
        allowed_operations="query",
        allowed_sandbox_modes=["structured_query"],
    )
    db_session.add(contract)
    await db_session.flush()
    session = SandboxSession(
        user_id=uuid.UUID(buyer_id),
        data_product_id=product.id,
        sandbox_level="L3",
        sandbox_mode="structured_query",
        contract_id=str(contract.id),
        status=SessionStatus.RUNNING.value,
    )
    db_session.add(session)
    await db_session.flush()

    result = await contract_fulfillment.check_contract_constraints(db_session, session.id, "query")

    assert result == {"allowed": True, "reason": "Contract constraints satisfied"}


@pytest.mark.asyncio
async def test_contract_fulfillment_fails_closed_when_runtime_provision_fails(db_session, make_user, monkeypatch):
    import app.services.contract_fulfillment as fulfillment_module

    product, provider_id = await _make_product(db_session, make_user)
    _, buyer_id = await make_user("buyer", "fulfill_buyer")
    contract = Contract(
        contract_no=f"FUL-{uuid.uuid4().hex[:12]}",
        contract_type=ContractType.DATA_QUERY.value,
        status=ContractStatus.ACTIVE.value,
        provider_id=provider_id,
        buyer_id=uuid.UUID(buyer_id),
        title="Provision Failed Contract",
        terms={},
        product_ids=[str(product.id)],
        allowed_operations="query",
        allowed_sandbox_levels="k8s",
        allowed_sandbox_modes=["structured_query"],
    )
    db_session.add(contract)
    await db_session.flush()

    async def fake_get_sandbox_manager():
        return FailedFulfillmentRuntime()

    monkeypatch.setattr(fulfillment_module, "get_sandbox_manager", fake_get_sandbox_manager)

    result = await contract_fulfillment.fulfill_contract(db_session, contract.id)

    assert result["status"] == "partial"
    assert result["provisioned_sessions"] == []
    assert result["errors"][0]["error"] == "runtime provision failed"

    sessions = (await db_session.execute(
        select(SandboxSession).where(SandboxSession.contract_id == str(contract.id))
    )).scalars().all()
    assert len(sessions) == 1
    assert sessions[0].status == SessionStatus.FAILED.value
    assert sessions[0].session_key_id is None

    key_result = await db_session.execute(select(KeyMetadata).where(KeyMetadata.session_id == sessions[0].id))
    assert key_result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_contract_fulfillment_fails_closed_when_key_distribution_fails(db_session, make_user, monkeypatch):
    import app.services.contract_fulfillment as fulfillment_module

    product, provider_id = await _make_product(db_session, make_user)
    _, buyer_id = await make_user("buyer", "fulfill_buyer")
    contract = Contract(
        contract_no=f"FUL-{uuid.uuid4().hex[:12]}",
        contract_type=ContractType.DATA_QUERY.value,
        status=ContractStatus.ACTIVE.value,
        provider_id=provider_id,
        buyer_id=uuid.UUID(buyer_id),
        title="Key Failed Contract",
        terms={},
        product_ids=[str(product.id)],
        allowed_operations="query",
        allowed_sandbox_levels="L3",
        allowed_sandbox_modes=["structured_query"],
    )
    db_session.add(contract)
    await db_session.flush()

    runtime = SuccessfulFulfillmentRuntime()
    kms = FailingFulfillmentKMS()

    async def fake_get_sandbox_manager():
        return runtime

    monkeypatch.setattr(fulfillment_module, "get_sandbox_manager", fake_get_sandbox_manager)
    monkeypatch.setattr(fulfillment_module, "kms_service", kms)

    result = await contract_fulfillment.fulfill_contract(db_session, contract.id)

    assert result["status"] == "partial"
    assert result["provisioned_sessions"] == []
    assert result["errors"][0]["error"] == "Session key distribution failed"
    assert runtime.terminated == ["fulfillment-container"]
    assert len(kms.destroyed) == 1

    session = (await db_session.execute(
        select(SandboxSession).where(SandboxSession.contract_id == str(contract.id))
    )).scalar_one()
    assert session.status == SessionStatus.FAILED.value
    assert session.container_id == "fulfillment-container"
    assert session.session_key_id is None

    key_result = await db_session.execute(select(KeyMetadata).where(KeyMetadata.session_id == session.id))
    assert key_result.scalar_one_or_none() is None
