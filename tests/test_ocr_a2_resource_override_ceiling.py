"""A2 (ocr CR 2026-10-18): 规格覆盖校验 fail-open —— 合约没写上限的键必须拒绝。

铁律：resource_override 只能向下收窄合约已批准的 sandbox_resource_spec。
修复前 `approved.get(key)` 不是数值就不比上限 ⇒ 合约缺该键 = 完全不设限，
当事方可提交 timeout_seconds=315360000 这类规格直落调度器。
"""
import uuid

import pytest

from app.models.contract import Contract
from app.models.user import User, UserRole
from app.services.auth_service import create_access_token, hash_password

pytestmark = pytest.mark.asyncio


async def _mk_user(db, role, prefix):
    unique = uuid.uuid4().hex[:8]
    user = User(
        username=f"{prefix}_{unique}", email=f"{prefix}_{unique}@example.com",
        hashed_password=hash_password("testpass123"), role=role,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user, {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


async def _mk_contract(db, provider, buyer, **overrides):
    payload = {
        "contract_no": f"CT-{uuid.uuid4().hex[:12]}",
        "contract_type": "data_usage",
        "status": "active",
        "provider_id": provider.id,
        "buyer_id": buyer.id,
        "title": "A2 规格上限测试合约",
        "product_ids": [str(uuid.uuid4())],
    }
    payload.update(overrides)
    contract = Contract(**payload)
    db.add(contract)
    await db.flush()
    await db.refresh(contract)
    return contract


async def _parties(db):
    provider, _ph = await _mk_user(db, UserRole.DATA_PROVIDER, "a2prov")
    buyer, headers = await _mk_user(db, UserRole.BUYER, "a2buy")
    return provider, buyer, headers


class TestResourceOverrideFailClosed:
    async def test_override_without_contract_ceiling_rejected(self, client, db_session):
        """合约整体没有 sandbox_resource_spec ⇒ 任何覆盖键都拒绝（fail-closed）。"""
        provider, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer)
        assert contract.sandbox_resource_spec in (None, {})

        resp = await client.post("/api/v1/compute/runs", json={
            "contract_id": str(contract.id),
            "resource_override": {"timeout_seconds": 315360000},
        }, headers=headers)
        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert body["code"] == "RESOURCE_OVERRIDE_EXCEEDED"
        assert "timeout_seconds" in str(body["detail"])

    async def test_override_key_missing_from_partial_spec_rejected(self, client, db_session):
        """合约批准了部分键 ⇒ 未批准的其它键同样不得放大（缺上限 ≠ 无限制）。"""
        provider, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(
            db_session, provider, buyer,
            sandbox_resource_spec={"cpu_cores": 2, "memory_mb": 2048},
        )

        resp = await client.post("/api/v1/compute/runs", json={
            "contract_id": str(contract.id),
            "resource_override": {"memory_mb": 1024, "timeout_seconds": 604800},
        }, headers=headers)
        assert resp.status_code == 400, resp.text
        assert resp.json()["code"] == "RESOURCE_OVERRIDE_EXCEEDED"

    async def test_override_non_numeric_ceiling_rejected(self, client, db_session):
        """上限值不是数值（脏数据/None）⇒ 拒绝，不做「跳过比较」。"""
        provider, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(
            db_session, provider, buyer,
            sandbox_resource_spec={"timeout_seconds": None},
        )
        resp = await client.post("/api/v1/compute/runs", json={
            "contract_id": str(contract.id),
            "resource_override": {"timeout_seconds": 7200},
        }, headers=headers)
        assert resp.status_code == 400, resp.text
        assert resp.json()["code"] == "RESOURCE_OVERRIDE_EXCEEDED"

    async def test_override_above_ceiling_still_rejected(self, client, db_session):
        """既有行为回归：有上限且超上限照旧拒绝。"""
        provider, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(
            db_session, provider, buyer,
            sandbox_resource_spec={"memory_mb": 2048},
        )
        resp = await client.post("/api/v1/compute/runs", json={
            "contract_id": str(contract.id),
            "resource_override": {"memory_mb": 4096},
        }, headers=headers)
        assert resp.status_code == 400, resp.text
        assert resp.json()["code"] == "RESOURCE_OVERRIDE_EXCEEDED"

    async def test_within_ceiling_and_no_override_still_accepted(self, client, db_session):
        """合法路径不得被收紧误伤：向下收窄的覆盖与不覆盖都照常受理。"""
        provider, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(
            db_session, provider, buyer,
            sandbox_resource_spec={"cpu_cores": 2, "memory_mb": 2048, "timeout_seconds": 3600},
        )

        narrowed = await client.post("/api/v1/compute/runs", json={
            "contract_id": str(contract.id),
            "resource_override": {"memory_mb": 1024, "timeout_seconds": 1800},
        }, headers=headers)
        assert narrowed.status_code == 200, narrowed.text

        plain = await client.post("/api/v1/compute/runs", json={
            "contract_id": str(contract.id),
        }, headers=headers)
        assert plain.status_code == 200, plain.text
