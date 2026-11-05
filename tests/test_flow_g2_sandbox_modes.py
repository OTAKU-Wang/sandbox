"""G2（走查 2026-11-05）：flow-config 可更新合约允许沙箱模式；null/清空=不改，未知模式 422。"""
import uuid

import pytest

from app.models.contract import Contract
from app.models.user import User, UserRole
from app.services.auth_service import create_access_token, hash_password

pytestmark = pytest.mark.asyncio


async def _mk_user(db, role: str, prefix: str):
    unique = uuid.uuid4().hex[:8]
    user = User(
        username=f"{prefix}_{unique}",
        email=f"{prefix}_{unique}@example.com",
        hashed_password=hash_password("testpass123"),
        role=role,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user, {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


async def _mk_contract(db, provider, buyer):
    contract = Contract(
        contract_no=f"CT-{uuid.uuid4().hex[:12]}",
        contract_type="data_usage",
        status="active",
        provider_id=provider.id,
        buyer_id=buyer.id,
        title="G2 模式白名单合约",
        product_ids=[str(uuid.uuid4())],
        allowed_sandbox_modes=["query"],
    )
    db.add(contract)
    await db.flush()
    await db.refresh(contract)
    return contract


async def _parties(db):
    provider, provider_headers = await _mk_user(db, UserRole.DATA_PROVIDER, "g2prov")
    buyer, buyer_headers = await _mk_user(db, UserRole.BUYER, "g2buy")
    return provider, provider_headers, buyer, buyer_headers


async def _flow_body(**extra):
    body = {
        "delivery_methods": ["dataset"],
        "auto_compute": False,
        "compute_frequency": "manual",
    }
    body.update(extra)
    return body


class TestFlowConfigSandboxModes:
    async def test_explicit_modes_are_updated_and_returned(self, client, db_session):
        provider, _, buyer, buyer_headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer)
        resp = await client.post(
            f"/api/v1/contracts/{contract.id}/flow-config",
            json=await _flow_body(**{"allowed_sandbox_modes": ["query", "develop"]}),
            headers=buyer_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["allowed_sandbox_modes"] == ["query", "develop"]

    async def test_missing_or_empty_keeps_current_modes(self, client, db_session):
        provider, _, buyer, buyer_headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer)

        omitted = await client.post(
            f"/api/v1/contracts/{contract.id}/flow-config",
            json=await _flow_body(),
            headers=buyer_headers,
        )
        assert omitted.status_code == 200, omitted.text
        assert omitted.json()["allowed_sandbox_modes"] == ["query"]

        cleared = await client.post(
            f"/api/v1/contracts/{contract.id}/flow-config",
            json=await _flow_body(**{"allowed_sandbox_modes": []}),
            headers=buyer_headers,
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["allowed_sandbox_modes"] == ["query"]

    async def test_unknown_mode_rejected_with_422(self, client, db_session):
        provider, _, buyer, buyer_headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer)
        resp = await client.post(
            f"/api/v1/contracts/{contract.id}/flow-config",
            json=await _flow_body(**{"allowed_sandbox_modes": ["telepathy"]}),
            headers=buyer_headers,
        )
        assert resp.status_code == 422, resp.text
