"""A168 (ocr-verified-findings §A168): OpenAPI 端点配额 calls_used 是读-改-写。

竞态窗口 = 装载行(open_api.py) 到 delivery_service.execute_endpoint 尾部自增之间的
整次沙箱执行；两个并发请求从同一个 calls_used 出发即可双双过闸并互相覆盖计数。
修法取台账给出的第一选项：装载端点行时 `SELECT ... FOR UPDATE`，把
「门禁判 + 自增 + 提交」整体串行化（PG 上行锁持续到请求事务提交）。

判别层是装载语句的行锁子句：本地是 SQLite(aiosqlite)，SQLite 无行锁语义，
FOR UPDATE 在该方言下编译为空 —— 时序竞态在本机两条路上都不可复现（见 NOTE），
故按 PG 生产方言断言锁子句真实进入 SQL；SQLite 方言下必须照常可编译（护栏，
否则本地/CI 直接坏）。
"""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite

from app.models.api_endpoint import ApiEndpoint
from app.models.contract import Contract
from app.models.user import User, UserRole
from app.services import delivery_service as dv
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
    provider, _ = await _mk_party(db, UserRole.DATA_PROVIDER, "a168prov")
    buyer, headers = await _mk_party(db, UserRole.BUYER, "a168buy")
    contract = Contract(
        contract_no=f"CT-A168-{uuid.uuid4().hex[:12]}",
        contract_type="data_usage",
        status="active",
        provider_id=provider.id,
        buyer_id=buyer.id,
        title="A168 测试合约",
        product_ids=[str(uuid.uuid4())],
        delivery_methods=["open_api"],
    )
    db.add(contract)
    await db.flush()
    endpoint = ApiEndpoint(
        owner_id=buyer.id,
        contract_id=contract.id,
        name="A168 日报",
        slug=f"a168-{uuid.uuid4().hex[:8]}",
        quota_total=1,
        calls_used=0,
    )
    db.add(endpoint)
    await db.flush()
    await db.refresh(endpoint)
    return buyer, headers, endpoint


class TestA168QuotaLoadLock:
    async def test_endpoint_load_takes_row_lock_for_quota_gate(self, client, db_session, monkeypatch):
        """装载端点行的 SELECT 必须携带行锁：无锁装载 = 配额门禁与自增之间的读-改-写竞态。"""
        _buyer, headers, endpoint = await _mk_endpoint(db_session)

        captured = []
        orig_execute = db_session.execute

        async def spy(stmt, *args, **kwargs):
            captured.append(stmt)
            return await orig_execute(stmt, *args, **kwargs)

        monkeypatch.setattr(db_session, "execute", spy)

        async def _fake_execute(db, ep, body, *, caller_id, workload=None):
            ep.calls_used = (ep.calls_used or 0) + 1
            await db.flush()
            return {"output_rows": 1, "duration_ms": 3, "request_sha256": "c" * 64}

        monkeypatch.setattr(dv, "execute_endpoint", _fake_execute)

        resp = await client.post(f"/open/v1/{endpoint.slug}", json={}, headers=headers)
        assert resp.status_code == 200, resp.text

        endpoint_selects = [
            s for s in captured
            if isinstance(s, select().__class__)
            and s.column_descriptions
            and s.column_descriptions[0].get("entity") is ApiEndpoint
        ]
        assert endpoint_selects, f"no ApiEndpoint select captured; got {captured!r}"
        lock_sql = str(endpoint_selects[0].compile(dialect=postgresql.dialect()))
        assert "FOR UPDATE" in lock_sql, (
            "端点装载必须是 SELECT ... FOR UPDATE（A168 台账修法一：行锁把"
            "配额门禁+自增串行化），实际 PG SQL: " + lock_sql
        )
        # 护栏：本地/CI 的 SQLite 方言必须可编译（该方言把行锁子句编译为空）。
        str(endpoint_selects[0].compile(dialect=sqlite.dialect()))
