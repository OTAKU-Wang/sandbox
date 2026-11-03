"""A145/A93 sibling pin — contract anchoring must be verifiable after a restart.

`ContractService.sign()` used to call `blockchain_service.anchor_to_blockchain()`,
whose `_client` is assigned exactly once (to `None`) so the real-chain branch is
unreachable and every call lands in `_anchor_local()`, an in-process dict. The
resulting `0x…` tx_hash was then written to `contracts.blockchain_tx_hash` as a
chain credential that no restart can verify, with no backend disclosure.

The fix routes contract anchoring through `blockchain_adapter` (PZ-03: every
backend persists its anchors and can re-hydrate after a restart) and records the
honest chain state alongside it. These tests assert the persisted anchor, the
restart-verification path, and that a failed anchor leaves the contract with NO
fabricated credential.
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.core import database as db_module
from app.models.blockchain_anchor import BlockchainAnchor
from app.models.contract import Contract, ContractStatus, ContractType
from app.models.data_product import DataProduct, DataProductStatus
from app.models.user import User
from app.services import contract_service as cs_module
from app.services.blockchain_adapter import (
    AnchorResult,
    ChainBackend,
    PGAppendOnlyAdapter,
    blockchain_adapter,
    chain_anchoring_state,
)


async def _signing_ready_contract(db_session, make_user):
    _, provider_id = await make_user("data_provider", "a93_provider")
    product = DataProduct(
        provider_id=uuid.UUID(provider_id),
        name="A93 Product",
        product_type="structured",
        status=DataProductStatus.PUBLISHED.value,
    )
    db_session.add(product)
    await db_session.flush()

    _, buyer_id = await make_user("buyer", "a93_buyer")
    contract = Contract(
        contract_no=f"A93-{uuid.uuid4().hex[:12]}",
        contract_type=ContractType.DATA_QUERY.value,
        status=ContractStatus.DRAFT.value,
        provider_id=uuid.UUID(provider_id),
        buyer_id=uuid.UUID(buyer_id),
        title="A93 Contract",
        terms={},
        product_ids=[str(product.id)],
        allowed_operations="query",
        allowed_sandbox_modes=["structured_query"],
    )
    db_session.add(contract)
    for uid in (uuid.UUID(provider_id), uuid.UUID(buyer_id)):
        user = await db_session.get(User, uid)
        user.sm2_certificate = f"pub-{uid.hex[:8]}"
    await db_session.flush()
    return contract


def _bind_adapter_to_test_db(monkeypatch):
    monkeypatch.setattr(blockchain_adapter, "_db_session_factory", db_module.async_session)
    monkeypatch.setattr(blockchain_adapter, "_hydrated", True)
    # Cold-start chain hydration opens its own session; on the suite's single
    # StaticPool connection that nested session would roll back the caller's
    # uncommitted work. Warm state is what a running process has anyway.
    monkeypatch.setattr(blockchain_adapter, "_last_hash", "prewarm-hash")
    monkeypatch.setattr(blockchain_adapter, "_chain", ["prewarm-anchor"])
    monkeypatch.setattr(cs_module.crypto_service, "verify", lambda data, sig, pk: True)


async def _sign_both_parties(db_session, contract):
    for uid in (contract.provider_id, contract.buyer_id):
        await cs_module.contract_service.sign(
            db_session,
            contract.id,
            uid,
            signature=f"04{uuid.uuid4().hex}",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )


@pytest.mark.asyncio
async def test_contract_anchor_is_persisted_not_process_local(db_session, make_user, monkeypatch):
    _bind_adapter_to_test_db(monkeypatch)
    contract = await _signing_ready_contract(db_session, make_user)

    await _sign_both_parties(db_session, contract)
    await db_session.flush()

    assert contract.status == ContractStatus.ACTIVE.value
    assert contract.blockchain_tx_hash, "active contract carries no anchoring credential"

    rows = list(
        (
            await db_session.execute(
                select(BlockchainAnchor).where(
                    BlockchainAnchor.tx_hash == contract.blockchain_tx_hash
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1, (
        "contract tx_hash has no persisted anchor row — unverifiable after a restart"
    )
    assert rows[0].backend == ChainBackend.PG_APPEND_ONLY.value
    assert chain_anchoring_state(ChainBackend(rows[0].backend)) == "pg-log"


@pytest.mark.asyncio
async def test_contract_anchor_verifies_from_a_fresh_adapter(db_session, make_user, monkeypatch):
    _bind_adapter_to_test_db(monkeypatch)
    contract = await _signing_ready_contract(db_session, make_user)

    await _sign_both_parties(db_session, contract)
    await db_session.flush()

    row = (
        await db_session.execute(
            select(BlockchainAnchor).where(
                BlockchainAnchor.tx_hash == contract.blockchain_tx_hash
            )
        )
    ).scalar_one()

    fresh = PGAppendOnlyAdapter()
    fresh.set_db_session_factory(db_module.async_session)
    record = await fresh._load_anchor_from_db(str(row.id))
    assert record is not None, "a restarted adapter cannot load the contract anchor"
    assert record.tx_hash == contract.blockchain_tx_hash
    assert record.data_hash == row.data_hash
    assert await fresh.verify(str(row.id), b"tampered-contract-payload") is False, (
        "a restarted adapter must reject a payload that does not match the anchor"
    )
    assert await fresh.verify(str(row.id), bytes.fromhex(record.data_hash)) is False


@pytest.mark.asyncio
async def test_failed_anchor_leaves_no_credential(db_session, make_user, monkeypatch):
    monkeypatch.setattr(cs_module.crypto_service, "verify", lambda data, sig, pk: True)

    async def _fail(data, metadata=None):
        return AnchorResult(success=False, error="chain backend down")

    monkeypatch.setattr(blockchain_adapter, "anchor", _fail)
    contract = await _signing_ready_contract(db_session, make_user)

    await _sign_both_parties(db_session, contract)
    await db_session.flush()

    assert contract.status == ContractStatus.ACTIVE.value
    assert contract.blockchain_tx_hash is None, (
        "a contract whose anchoring failed must not carry a tx credential"
    )
