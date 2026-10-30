"""A147 — a side effect after the container is up must not leak the container.

`ContractFulfillmentService._provision_product_session()` creates a runtime
container outside the DB transaction. Only one failure path (distribute_key
returning empty) used to run the cleanup; every other step after the container
exists — generate_session_key, distribute_key raising, KeyMetadata persistence,
refresh — propagated straight to `fulfill_contract()`, which merely appends to
`errors`. Because the runtime side effect is not part of the DB transaction,
the outer rollback deletes the session row while the container keeps running
with its data channel injected, and nothing points at it any more.

The fix wraps everything after the container exists in one cleanup path:
destroy the session key, terminate the container, mark the session FAILED with
the cause, then re-raise. These tests drive that path with fake runtime/KMS
objects (same harness shape as tests/test_contract_fulfillment.py).
"""
import uuid

import pytest
from sqlalchemy import select

from app.models.contract import Contract, ContractStatus, ContractType
from app.models.data_product import DataProduct, DataProductStatus
from app.models.kms import KeyMetadata
from app.models.sandbox_session import SandboxSession, SessionStatus
from app.services import contract_fulfillment as cf_module
from app.services.contract_fulfillment import contract_fulfillment


class RecordingRuntime:
    def __init__(self, container_id: str = "a147-container", terminate_error: Exception | None = None):
        self.container_id = container_id
        self.terminated: list[str] = []
        self._terminate_error = terminate_error

    def provision(self, **kwargs):
        return {"container_id": self.container_id, "status": "running"}

    def terminate(self, container_id: str):
        if self._terminate_error is not None:
            raise self._terminate_error
        self.terminated.append(container_id)
        return True


class RaisingKMS:
    def __init__(self, fail_at: str):
        self.fail_at = fail_at
        self.destroyed: list[str] = []

    def generate_session_key(self, session_id: str):
        if self.fail_at == "generate":
            raise RuntimeError("kms generate unavailable")
        return {"key_id": f"session-{session_id}-key", "key_bytes": b"0" * 32}

    def distribute_key(self, key_id: str, session_id: str, **kwargs):
        if self.fail_at == "distribute":
            raise RuntimeError("kms distribute rejected")
        return {"key_id": key_id}

    def destroy_key(self, key_id: str):
        self.destroyed.append(key_id)
        return True

    def export_wrapped(self, key_id: str):
        return "wrapped"

    def export_sm2_ciphertext(self, key_id: str):
        return "cipher"


async def _signed_contract(db_session, make_user):
    _, provider_id = await make_user("data_provider", "a147_provider")
    product = DataProduct(
        provider_id=uuid.UUID(provider_id),
        name="A147 Product",
        product_type="structured",
        status=DataProductStatus.PUBLISHED.value,
    )
    db_session.add(product)
    await db_session.flush()

    _, buyer_id = await make_user("buyer", "a147_buyer")
    contract = Contract(
        contract_no=f"A147-{uuid.uuid4().hex[:12]}",
        contract_type=ContractType.DATA_QUERY.value,
        status=ContractStatus.ACTIVE.value,
        provider_id=uuid.UUID(provider_id),
        buyer_id=uuid.UUID(buyer_id),
        title="A147 Contract",
        terms={},
        product_ids=[str(product.id)],
        allowed_operations="query",
        allowed_sandbox_modes=["structured_query"],
        allowed_sandbox_levels="L3",
        max_duration_hours=24,
    )
    db_session.add(contract)
    await db_session.flush()
    return contract, product


def _patch(monkeypatch, runtime, kms):
    async def _manager():
        return runtime

    monkeypatch.setattr(cf_module, "get_sandbox_manager", _manager)
    monkeypatch.setattr(cf_module, "kms_service", kms)


@pytest.mark.asyncio
async def test_generate_key_failure_terminates_container(db_session, make_user, monkeypatch):
    contract, _product = await _signed_contract(db_session, make_user)
    runtime = RecordingRuntime()
    kms = RaisingKMS("generate")
    _patch(monkeypatch, runtime, kms)

    result = await contract_fulfillment.fulfill_contract(db_session, contract.id)

    assert result["status"] == "partial"
    assert any("kms generate unavailable" in e["error"] for e in result["errors"]), result["errors"]
    assert runtime.terminated == ["a147-container"], (
        "container left running after a post-provision failure"
    )
    session = (await db_session.execute(select(SandboxSession))).scalar_one()
    assert session.status == SessionStatus.FAILED.value
    assert "kms generate unavailable" in (session.error_message or "")
    assert (await db_session.execute(select(KeyMetadata))).scalars().all() == []


@pytest.mark.asyncio
async def test_distribute_key_exception_terminates_container_and_destroys_key(
    db_session, make_user, monkeypatch
):
    contract, _product = await _signed_contract(db_session, make_user)
    runtime = RecordingRuntime()
    kms = RaisingKMS("distribute")
    _patch(monkeypatch, runtime, kms)

    result = await contract_fulfillment.fulfill_contract(db_session, contract.id)

    assert result["status"] == "partial"
    assert any("kms distribute rejected" in e["error"] for e in result["errors"]), result["errors"]
    assert runtime.terminated == ["a147-container"]
    assert len(kms.destroyed) == 1, "issued session key not destroyed"
    session = (await db_session.execute(select(SandboxSession))).scalar_one()
    assert session.status == SessionStatus.FAILED.value
    assert session.session_key_id is None


@pytest.mark.asyncio
async def test_key_metadata_persist_failure_terminates_container(
    db_session, make_user, monkeypatch
):
    contract, _product = await _signed_contract(db_session, make_user)
    runtime = RecordingRuntime()
    kms = RaisingKMS("none")
    _patch(monkeypatch, runtime, kms)

    def _boom(*args, **kwargs):
        raise RuntimeError("key metadata insert failed")

    monkeypatch.setattr(cf_module, "KeyMetadata", _boom)

    result = await contract_fulfillment.fulfill_contract(db_session, contract.id)

    assert result["status"] == "partial"
    assert any("key metadata insert failed" in e["error"] for e in result["errors"]), result["errors"]
    assert runtime.terminated == ["a147-container"]
    session = (await db_session.execute(select(SandboxSession))).scalar_one()
    assert session.status == SessionStatus.FAILED.value


@pytest.mark.asyncio
async def test_terminate_failure_does_not_skip_key_destruction_or_session_state(
    db_session, make_user, monkeypatch
):
    contract, _product = await _signed_contract(db_session, make_user)
    runtime = RecordingRuntime(terminate_error=RuntimeError("cube-master unreachable"))
    kms = RaisingKMS("generate")
    _patch(monkeypatch, runtime, kms)

    result = await contract_fulfillment.fulfill_contract(db_session, contract.id)

    assert result["status"] == "partial"
    assert any("cube-master unreachable" in e["error"] for e in result["errors"]), (
        "terminate failure must be disclosed in the fulfillment result"
    )
    session = (await db_session.execute(select(SandboxSession))).scalar_one()
    assert session.status == SessionStatus.FAILED.value
    assert "kms generate unavailable" in (session.error_message or "")


@pytest.mark.asyncio
async def test_empty_distribution_path_still_cleans_up(db_session, make_user, monkeypatch):
    contract, _product = await _signed_contract(db_session, make_user)
    runtime = RecordingRuntime()
    kms = RaisingKMS("none")

    def _empty(key_id, session_id, **kwargs):
        return None

    monkeypatch.setattr(kms, "distribute_key", _empty)
    _patch(monkeypatch, runtime, kms)

    result = await contract_fulfillment.fulfill_contract(db_session, contract.id)

    assert result["status"] == "partial"
    assert runtime.terminated == ["a147-container"]
    session = (await db_session.execute(select(SandboxSession))).scalar_one()
    assert session.status == SessionStatus.FAILED.value
    assert session.error_message == "Session key distribution failed"
