"""Dev sandbox contract enforcement tests (gap A3).

The dev-sandbox session creation path must require a contract covering the
attached data product when DEV_SANDBOX_REQUIRE_CONTRACT is enabled, and the
DP budget must be inherited from the contract rather than self-declared.
The sandbox runtime provisioning is mocked — these tests focus on the
enforcement logic, not on sandbox mechanics.
"""
import uuid

import pytest

from app.core.config import get_settings
from app.models.contract import Contract, ContractStatus
from app.models.data_product import DataProduct


async def _mk_contract(db_session, provider_id, buyer_id, product_ids, *, status=ContractStatus.ACTIVE.value, dp_epsilon_budget=0.5):
    contract = Contract(
        contract_no=f"C-{uuid.uuid4().hex[:8]}",
        contract_type="data_query",
        provider_id=uuid.UUID(provider_id),
        buyer_id=uuid.UUID(buyer_id),
        title="dev-gate",
        product_ids=[str(pid) for pid in product_ids],
        status=status,
        dp_epsilon_budget=dp_epsilon_budget,
    )
    db_session.add(contract)
    await db_session.flush()
    await db_session.refresh(contract)
    return contract


def test_dev_dp_budget_inherits_from_contract():
    from app.api.dev_sandbox import _dev_dp_budget, CreateDevSessionRequest

    class _C:
        dp_epsilon_budget = 0.5

    class _C_None:
        dp_epsilon_budget = None

    body = CreateDevSessionRequest(mode="structured", dp_epsilon_budget=999)
    # contract governs → request value ignored, contract budget wins
    assert _dev_dp_budget(body, _C()) == 0.5
    # contract without budget → no DP allowed in governed dev sessions
    assert _dev_dp_budget(body, _C_None()) is None
    # no contract (gate disabled / pure dev) → request value still honored
    assert _dev_dp_budget(body, None) == 999


@pytest.mark.asyncio
async def test_dev_session_requires_contract_when_enabled(client, db_session, make_user, monkeypatch):
    from app.services.data_product_sandbox import dev_sandbox

    monkeypatch.setattr(get_settings(), "DEV_SANDBOX_REQUIRE_CONTRACT", True)

    def _fake_provision(session_id, level, data_path, timeout, user_id=""):
        return {"container_id": "l3-fake", "status": "running", "attestation_quote": None}
    monkeypatch.setattr(dev_sandbox.runtime, "provision", _fake_provision)

    headers, provider_id = await make_user("data_provider", "prov")
    _, buyer_id = await make_user("buyer", "buy")

    product = DataProduct(provider_id=uuid.UUID(provider_id), name="dp", status="published")
    db_session.add(product)
    await db_session.flush()
    await db_session.refresh(product)

    # 1) data product attached but no contract_id → 400
    resp = await client.post("/api/v1/dev-sandbox/sessions", json={
        "mode": "structured",
        "data_product_id": str(product.id),
        "dp_epsilon_budget": 999,
    }, headers=headers)
    assert resp.status_code == 400
    assert "contract_id" in resp.json().get("detail", "")

    # 2) contract does not cover the product → 403
    other_contract = await _mk_contract(
        db_session, provider_id, buyer_id, product_ids=[uuid.uuid4()],
    )
    resp = await client.post("/api/v1/dev-sandbox/sessions", json={
        "mode": "structured",
        "data_product_id": str(product.id),
        "contract_id": str(other_contract.id),
    }, headers=headers)
    assert resp.status_code == 403

    # 3) draft contract → 403
    draft_contract = await _mk_contract(
        db_session, provider_id, buyer_id, product_ids=[product.id],
        status=ContractStatus.DRAFT.value,
    )
    resp = await client.post("/api/v1/dev-sandbox/sessions", json={
        "mode": "structured",
        "data_product_id": str(product.id),
        "contract_id": str(draft_contract.id),
    }, headers=headers)
    assert resp.status_code == 403

    # 4) valid contract covering the product → 201, contract recorded
    contract = await _mk_contract(
        db_session, provider_id, buyer_id, product_ids=[product.id],
    )
    resp = await client.post("/api/v1/dev-sandbox/sessions", json={
        "mode": "structured",
        "data_product_id": str(product.id),
        "contract_id": str(contract.id),
        "dp_epsilon_budget": 999,  # must be ignored in favour of the contract
    }, headers=headers)
    assert resp.status_code == 201
    session = await dev_sandbox.get_session(resp.json()["session_id"])
    assert session is not None
    assert session.metadata.get("contract_id") == str(contract.id)
    assert session.config.dp_epsilon_budget == 0.5  # contract value, not 999


@pytest.mark.asyncio
async def test_dev_session_without_data_product_not_gated(client, db_session, make_user, monkeypatch):
    """Pure development mode (no data product) is not contract-gated."""
    from app.services.data_product_sandbox import dev_sandbox

    monkeypatch.setattr(get_settings(), "DEV_SANDBOX_REQUIRE_CONTRACT", True)

    def _fake_provision(session_id, level, data_path, timeout, user_id=""):
        return {"container_id": "l3-fake", "status": "running", "attestation_quote": None}
    monkeypatch.setattr(dev_sandbox.runtime, "provision", _fake_provision)

    headers, _ = await make_user("data_provider", "prov")

    resp = await client.post("/api/v1/dev-sandbox/sessions", json={
        "mode": "structured",
    }, headers=headers)
    assert resp.status_code == 201
