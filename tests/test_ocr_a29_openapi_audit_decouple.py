"""A29 (ocr-verified-findings §A29): OpenAPI 端点的审计写入与业务提交互相绑死。

判据（与 audit_service.log_standalone docstring + app/api/AGENTS.md 的既有契约一致）：
1. 失败路径：`EndpointUnavailable` 之后写审计 —— 审计一失败，正确的 429+code
   不能变成裸 500（旧写法 `log(db)+commit` 在审计抛错时把拒访答案吞掉）。
2. 成功路径：沙箱已真实执行，`calls_used`/计量是既成事实 —— 审计一失败，
   已执行调用的配额/计量不能随未提交事务一起丢失（客户端按 5xx 重试会再
   真实执行一次）。业务提交照常，审计失败仅降级为 [CRITICAL] 日志 + 独立会话补写。
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models.api_endpoint import ApiEndpoint
from app.models.contract import Contract
from app.models.user import User, UserRole
from app.services import delivery_service as dv
from app.services.audit_service import audit_service
from app.services.auth_service import create_access_token, hash_password

pytestmark = pytest.mark.asyncio


async def _mk_party(db, role, prefix):
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


async def _mk_endpoint(db):
    provider, _ = await _mk_party(db, UserRole.DATA_PROVIDER, "a29prov")
    buyer, headers = await _mk_party(db, UserRole.BUYER, "a29buy")
    contract = Contract(
        contract_no=f"CT-A29-{uuid.uuid4().hex[:12]}",
        contract_type="data_usage",
        status="active",
        provider_id=provider.id,
        buyer_id=buyer.id,
        title="A29 测试合约",
        product_ids=[str(uuid.uuid4())],
        delivery_methods=["open_api"],
    )
    db.add(contract)
    await db.flush()
    endpoint = ApiEndpoint(
        owner_id=buyer.id,
        contract_id=contract.id,
        name="A29 日报",
        slug=f"a29-{uuid.uuid4().hex[:8]}",
        quota_total=10,
    )
    db.add(endpoint)
    await db.flush()
    await db.refresh(endpoint)
    return buyer, headers, endpoint


async def _audit_boom(db, *args, **kwargs):
    raise RuntimeError("audit store down (A29 probe)")


class TestA29AuditDecoupling:
    async def test_refused_call_keeps_429_when_audit_write_fails(self, client, db_session, monkeypatch):
        """失败路径：审计写失败必须原样返回 429 + QUOTA_EXCEEDED，而不是 500/裸异常。"""
        _buyer, headers, endpoint = await _mk_endpoint(db_session)

        async def _refuse(db, ep, body, *, caller_id, workload=None):
            raise dv.EndpointUnavailable("QUOTA_EXCEEDED", "调用配额已用尽")

        monkeypatch.setattr(dv, "execute_endpoint", _refuse)
        monkeypatch.setattr(audit_service, "log", _audit_boom)

        resp = await client.post(f"/open/v1/{endpoint.slug}", json={}, headers=headers)
        assert resp.status_code == 429, f"expected 429, got {resp.status_code}: {resp.text}"
        assert resp.json()["code"] == "QUOTA_EXCEEDED"

    async def test_executed_call_metering_survives_audit_failure(self, client, db_session, monkeypatch):
        """成功路径：沙箱已执行，审计写失败不得回滚/丢弃已执行调用的配额与计量。"""
        _buyer, headers, endpoint = await _mk_endpoint(db_session)

        async def _fake_execute(db, ep, body, *, caller_id, workload=None):
            # 与 delivery_service.execute_endpoint 的尾部一致：调用后在请求会话里
            # 读-改-写 calls_used 并 flush，提交靠 open_api 的 db.commit()。
            ep.calls_used = (ep.calls_used or 0) + 1
            ep.last_called_at = datetime.now(timezone.utc)
            await db.flush()
            return {"output_rows": 3, "duration_ms": 7, "request_sha256": "a" * 64}

        monkeypatch.setattr(dv, "execute_endpoint", _fake_execute)
        monkeypatch.setattr(audit_service, "log", _audit_boom)

        resp = await client.post(f"/open/v1/{endpoint.slug}", json={}, headers=headers)
        assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"

        await db_session.refresh(endpoint)
        assert endpoint.calls_used == 1, (
            "已真实执行的调用必须计入配额（审计失败不能把计量一起回滚）"
        )

    async def test_happy_path_still_records_endpoint_scoped_audit(self, client, db_session, monkeypatch):
        """回归护栏：审计正常时成功路径仍在请求事务里落 openapi.call（app/api/AGENTS.md 口径）。"""
        from app.models.audit_log import AuditLog

        _buyer, headers, endpoint = await _mk_endpoint(db_session)

        async def _fake_execute(db, ep, body, *, caller_id, workload=None):
            ep.calls_used = (ep.calls_used or 0) + 1
            await db.flush()
            return {"output_rows": 2, "duration_ms": 9, "request_sha256": "b" * 64}

        monkeypatch.setattr(dv, "execute_endpoint", _fake_execute)

        resp = await client.post(f"/open/v1/{endpoint.slug}", json={}, headers=headers)
        assert resp.status_code == 200, resp.text
        row = (await db_session.execute(
            select(AuditLog).where(AuditLog.action == "openapi.call")
        )).scalar_one()
        assert row.resource_type == "api_endpoint"
        assert row.resource_id == str(endpoint.id)
