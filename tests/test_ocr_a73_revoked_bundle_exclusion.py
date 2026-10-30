"""A73 — 已撤销（revoked/expired）的策略包不得继续参与授权决策，且排除必须披露。

潜伏缺陷（docs/ocr-review-2026-10-18 fix-queue，A73/A125 同族）：DB 侧 PDP
`PolicyEvaluator.evaluate` 按 `version desc limit 1` 取包时**不过滤 `revoked_at`**，
也不看合约是否已 TERMINATED/过期 ⇒ 合约终止级联（`contract_service.terminate`）
写好的 `revoked_at` 在读侧不存在，撤销包继续产出 ALLOW；活路径 PDP
（`policy_compiler.py`）倒是有「Contract not active」位，读侧两侧原本都对不上。

修法：
- 参与决策的包必须 `revoked_at IS NULL` 且所属合约未 TERMINATED、未过 `valid_until`；
- 被排除的包（撤销/终止/过期）逐个披露：进 `PolicyDecision.excluded_bundles` 与
  决策审计行 `policy.bundles_excluded`（不静默）；
- 撤销全部包 ⇒ DENY，且 reason 里说明「存在已撤销包未参与决策」。
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.contract import Contract, ContractStatus
from app.models.policy_bundle import PolicyBundle
from app.services.audit_service import audit_service
from app.services.policy_evaluator import AccessRequest, Decision, PolicyEvaluator

EXCLUSION_ACTION = "policy.bundles_excluded"
_HASH = "a" * 64  # 与 patch 后的 sm3_hash 输出一致，避免宿主 gmssl 差异


@pytest.fixture
def evaluator():
    return PolicyEvaluator()


@pytest.fixture(autouse=True)
def _no_live_opa(monkeypatch):
    """同 tests/test_policy_evaluator.py：钉死 OPA 传输边界，确定性走 Python 回落。"""
    async def _push_unavailable(*args, **kwargs):
        return False

    from app.services.opa_client import opa_client

    monkeypatch.setattr(opa_client, "push_policy", _push_unavailable)


async def _mk_contract(db_session, provider_id, buyer_id, *,
                       status=ContractStatus.ACTIVE.value, valid_until=None):
    contract = Contract(
        contract_no=f"C-{uuid.uuid4().hex[:8]}",
        contract_type="data_query",
        provider_id=uuid.UUID(provider_id),
        buyer_id=uuid.UUID(buyer_id),
        title="a73-test",
        product_ids=[str(uuid.uuid4())],
        status=status,
        valid_until=valid_until,
        max_output_rows=10,
    )
    db_session.add(contract)
    await db_session.flush()
    await db_session.refresh(contract)
    return contract


async def _mk_bundle(db_session, contract, *, version=1, revoked_at=None):
    bundle = PolicyBundle(
        policy_id=f"cds/policies/contract_{contract.id}_v{version}",
        contract_id=contract.id,
        rego_source="package cds.policies.test\ndefault allow = false",
        integrity_hash=_HASH,
        field_acl=None,
        allowed_ops=["read", "query"],
        sandbox_modes=["query"],
        version=version,
        revoked_at=revoked_at,
    )
    db_session.add(bundle)
    await db_session.flush()
    await db_session.refresh(bundle)
    return bundle


def _req(contract, user_id):
    return AccessRequest(
        user_id=uuid.UUID(user_id),
        contract_id=contract.id,
        operation="read",
        sandbox_mode="query",
    )


async def _evaluate(evaluator, request, db):
    with patch("app.utils.crypto.sm3_hash", return_value=_HASH):
        return await evaluator.evaluate(request, db)


async def _exclusion_rows(db, contract_id):
    rows = (await db.execute(
        select(AuditLog).where(
            AuditLog.action == EXCLUSION_ACTION,
            AuditLog.resource_id == str(contract_id),
        )
    )).scalars().all()
    return rows


class TestRevokedBundleExclusion:
    @pytest.mark.asyncio
    async def test_active_bundle_allows_then_revoked_denies(self, evaluator, db_session, make_user):
        """撤销前后对比：同一请求撤销前 ALLOW、撤销后 DENY，且披露排除的包。"""
        _, provider = await make_user("data_provider", "a73prov")
        _, buyer = await make_user("buyer", "a73buy")
        contract = await _mk_contract(db_session, provider, buyer)
        bundle = await _mk_bundle(db_session, contract)

        decision = await _evaluate(evaluator, _req(contract, buyer), db_session)
        assert decision.decision == Decision.ALLOW
        assert decision.excluded_bundles == []

        # 撤销（contract_service.terminate 级联写的就是这个标记）
        bundle.revoked_at = datetime.now(timezone.utc)
        await db_session.flush()

        decision = await _evaluate(evaluator, _req(contract, buyer), db_session)
        assert decision.decision == Decision.DENY
        assert "revoked" in decision.reason.lower()
        assert [e["policy_id"] for e in decision.excluded_bundles] == [bundle.policy_id]
        assert decision.excluded_bundles[0]["cause"] == "revoked"

        # 决策审计必须披露「存在已撤销包未参与决策」——不静默
        audits = await _exclusion_rows(db_session, contract.id)
        assert len(audits) == 1
        detail = audits[0].detail
        excluded = detail["excluded_bundles"]
        assert excluded[0]["policy_id"] == bundle.policy_id
        assert excluded[0]["cause"] == "revoked"

    @pytest.mark.asyncio
    async def test_terminate_cascade_bundle_no_longer_participates(self, evaluator, db_session, make_user):
        """经 contract_service.terminate 真实级联撤销后，PDP 不再据此 ALLOW。"""
        from app.services.contract_service import contract_service

        _, provider = await make_user("data_provider", "a73prov2")
        _, buyer = await make_user("buyer", "a73buy2")
        contract = await _mk_contract(db_session, provider, buyer)
        bundle = await _mk_bundle(db_session, contract)

        decision = await _evaluate(evaluator, _req(contract, buyer), db_session)
        assert decision.decision == Decision.ALLOW

        await contract_service.terminate(db_session, contract.id, uuid.UUID(provider))
        await db_session.flush()
        await db_session.refresh(bundle)
        assert bundle.revoked_at is not None  # 写侧行为（A1/A160c 已钉），此处读侧收口

        decision = await _evaluate(evaluator, _req(contract, buyer), db_session)
        assert decision.decision == Decision.DENY
        assert bundle.policy_id in [e["policy_id"] for e in decision.excluded_bundles]
        assert len(await _exclusion_rows(db_session, contract.id)) >= 1

    @pytest.mark.asyncio
    async def test_expired_contract_bundle_excluded_before_sweep(self, evaluator, db_session, make_user):
        """过期但清扫循环未来得及 revoke 的窗口：过 valid_until 的包同样不参与。"""
        _, provider = await make_user("data_provider", "a73prov3")
        _, buyer = await make_user("buyer", "a73buy3")
        past = datetime.now(timezone.utc) - timedelta(days=1)
        contract = await _mk_contract(db_session, provider, buyer, valid_until=past)
        bundle = await _mk_bundle(db_session, contract)  # revoked_at 仍为 NULL

        decision = await _evaluate(evaluator, _req(contract, buyer), db_session)
        assert decision.decision == Decision.DENY
        causes = {e["policy_id"]: e["cause"] for e in decision.excluded_bundles}
        assert causes[bundle.policy_id] == "contract_expired"
        assert len(await _exclusion_rows(db_session, contract.id)) == 1

    @pytest.mark.asyncio
    async def test_terminated_contract_unrevoked_bundle_excluded(self, evaluator, db_session, make_user):
        """竞态兜底：合约已 TERMINATED 而包 revoked_at 还没写上 ⇒ 也不参与。"""
        _, provider = await make_user("data_provider", "a73prov4")
        _, buyer = await make_user("buyer", "a73buy4")
        contract = await _mk_contract(db_session, provider, buyer,
                                      status=ContractStatus.TERMINATED.value)
        bundle = await _mk_bundle(db_session, contract)

        decision = await _evaluate(evaluator, _req(contract, buyer), db_session)
        assert decision.decision == Decision.DENY
        causes = {e["policy_id"]: e["cause"] for e in decision.excluded_bundles}
        assert causes[bundle.policy_id] == "contract_terminated"

    @pytest.mark.asyncio
    async def test_newer_revoked_older_active_newer_not_used(self, evaluator, db_session, make_user):
        """排除≠全拒：最新包被撤销时，较新的有效包照常参与，被撤销者仍披露。"""
        _, provider = await make_user("data_provider", "a73prov5")
        _, buyer = await make_user("buyer", "a73buy5")
        contract = await _mk_contract(db_session, provider, buyer)
        old = await _mk_bundle(db_session, contract, version=1)
        new = await _mk_bundle(db_session, contract, version=2,
                               revoked_at=datetime.now(timezone.utc))

        decision = await _evaluate(evaluator, _req(contract, buyer), db_session)
        assert decision.decision == Decision.ALLOW  # v1 仍有效
        assert [e["policy_id"] for e in decision.excluded_bundles] == [new.policy_id]

    @pytest.mark.asyncio
    async def test_no_bundles_no_exclusion_noise(self, evaluator, db_session, make_user):
        """根本没有包 ⇒ 旧 DENY 语义原样，不产生排除审计（披露只针对真实排除）。"""
        _, provider = await make_user("data_provider", "a73prov6")
        _, buyer = await make_user("buyer", "a73buy6")
        contract = await _mk_contract(db_session, provider, buyer)

        decision = await _evaluate(evaluator, _req(contract, buyer), db_session)
        assert decision.decision == Decision.DENY
        assert decision.reason == "No active policy bundle found for contract"
        assert decision.excluded_bundles == []
        assert await _exclusion_rows(db_session, contract.id) == []

    @pytest.mark.asyncio
    async def test_mocked_db_compat(self, evaluator):
        """沿用 test_policy_evaluator 的全 mock db：排除查询必须容空，不炸 mock 路径。"""
        db = AsyncMock()
        res = MagicMock()
        res.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=res)
        request = AccessRequest(
            user_id=uuid.uuid4(), contract_id=uuid.uuid4(),
            operation="read", sandbox_mode="query",
        )
        with patch("app.utils.crypto.sm3_hash", return_value=_HASH):
            decision = await evaluator.evaluate(request, db)
        assert decision.decision == Decision.DENY
        assert decision.excluded_bundles == []
