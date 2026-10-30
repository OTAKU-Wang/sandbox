"""A11 — OpenAPI 端点 slug 唯一性检查：单行探测 + 竞态落 409（不整表物化、不落 500）。

判据（对应 `app/api/deliveries.py::create_openapi_endpoint`）：
1. 创建端点时打到 `api_endpoints` 的 SELECT 必须带 slug 谓词。旧写法
   `select(ApiEndpoint)` 无 WHERE 把整表读成 ORM 对象集，只为算一个「slug 是否被占」的
   布尔判断——代价随行数线性增长，且与唯一性判定毫无关系。
2. 探测与 INSERT 之间的同瞬并发（另一事务在探测之后提交了同名 slug）由唯一约束
   `uq_api_endpoints_slug` 收敛为 409 `ENDPOINT_SLUG_TAKEN`，而不是让 DBAPI 的
   IntegrityError 冒到全局 500。
3. 未显式给 slug 时的自动派生语义不变：与既有 slug 冲突则加 `-2`/`-3`… 后缀。
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import Select, event, false

from app.models.api_endpoint import ApiEndpoint
from app.models.contract import Contract, DeliveryMethod, WorkloadEnv
from app.models.user import User, UserRole
from app.models.workload import Workload, WorkloadStatus
from app.services.auth_service import create_access_token, hash_password
from tests.conftest import test_engine

pytestmark = pytest.mark.asyncio

ENDPOINT_URL = "/api/v1/deliveries/openapi/endpoints"


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


async def _setup(db):
    provider, _ = await _mk_user(db, UserRole.DATA_PROVIDER, "a11prov")
    buyer, headers = await _mk_user(db, UserRole.BUYER, "a11buy")
    contract = Contract(
        contract_no=f"CT-{uuid.uuid4().hex[:12]}",
        contract_type="data_usage",
        status="active",
        provider_id=provider.id,
        buyer_id=buyer.id,
        title="A11 测试合约",
        product_ids=[str(uuid.uuid4())],
        delivery_methods=[DeliveryMethod.OPEN_API.value],
    )
    db.add(contract)
    await db.flush()
    workload = Workload(
        name="A11 生产负载",
        user_id=buyer.id,
        contract_id=contract.id,
        entrypoint="print(1)",
        entrypoint_hash="0" * 64,
        env=WorkloadEnv.PRODUCTION.value,
        status=WorkloadStatus.PUBLISHED.value,
        is_latest=True,
        published_at=datetime.now(timezone.utc),
    )
    db.add(workload)
    await db.flush()
    return buyer, headers, contract, workload


def _seed_endpoint(db, contract, owner_id, slug) -> ApiEndpoint:
    endpoint = ApiEndpoint(
        owner_id=owner_id,
        contract_id=contract.id,
        name=f"seed-{slug}",
        slug=slug,
    )
    db.add(endpoint)
    return endpoint


@pytest.fixture
def captured_sql():
    seen: list[str] = []

    def _before(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(test_engine.sync_engine, "before_cursor_execute", _before)
    try:
        yield seen
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", _before)


def _endpoint_selects(statements: list[str]) -> list[str]:
    return [s for s in statements if s.lstrip().upper().startswith("SELECT") and "FROM api_endpoints" in s]


class TestSlugCheckScope:
    async def test_slug_probe_is_scoped_not_full_table(self, client, db_session, captured_sql):
        buyer, headers, contract, workload = await _setup(db_session)
        for i in range(50):
            _seed_endpoint(db_session, contract, buyer.id, f"seed-{i}")
        await db_session.flush()

        resp = await client.post(
            ENDPOINT_URL,
            json={
                "contract_id": str(contract.id),
                "workload_id": str(workload.id),
                "name": "日报 API",
                "slug": "daily-report",
            },
            headers=headers,
        )
        assert resp.status_code == 201, resp.text

        probes = _endpoint_selects(captured_sql)
        assert probes, "创建路径应当对 api_endpoints 做一次 slug 探测"
        for stmt in probes:
            assert "WHERE" in stmt, f"slug 检查退化为整表扫描：{stmt!r}"
            assert "api_endpoints.slug" in stmt, f"slug 探测未按 slug 收敛：{stmt!r}"

    async def test_generated_slug_still_avoids_collisions(self, client, db_session, captured_sql):
        buyer, headers, contract, workload = await _setup(db_session)
        _seed_endpoint(db_session, contract, buyer.id, "report")
        await db_session.flush()

        resp = await client.post(
            ENDPOINT_URL,
            json={"contract_id": str(contract.id), "workload_id": str(workload.id), "name": "Report"},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["slug"] == "report-2"
        for stmt in _endpoint_selects(captured_sql):
            assert "WHERE" in stmt, f"派生 slug 的冲突探测退化为整表扫描：{stmt!r}"


class TestSlugRace:
    async def test_concurrent_same_slug_is_409_not_500(self, client, db_session):
        """同瞬并发：探测读不到对方刚提交的同名行，唯一约束必须把它接成 409。

        「对方事务刚提交」用 do_orm_execute 给 api_endpoints 的 SELECT 追加恒假谓词来
        模拟——读侧看不见、写侧仍受 uq_api_endpoints_slug 约束，等价于 check-then-act
        窗口里对手先落库。
        """
        buyer, headers, contract, workload = await _setup(db_session)
        _seed_endpoint(db_session, contract, buyer.id, "daily-report")
        await db_session.flush()

        def _blind_endpoint_reads(ctx):
            if not ctx.is_select:
                return
            stmt = ctx.statement
            if isinstance(stmt, Select) and any(
                getattr(froms, "name", None) == "api_endpoints" for froms in stmt.get_final_froms()
            ):
                ctx.statement = stmt.where(false())

        event.listen(db_session.sync_session, "do_orm_execute", _blind_endpoint_reads)
        try:
            resp = await client.post(
                ENDPOINT_URL,
                json={
                    "contract_id": str(contract.id),
                    "workload_id": str(workload.id),
                    "name": "日报 API",
                    "slug": "daily-report",
                },
                headers=headers,
            )
        finally:
            event.remove(db_session.sync_session, "do_orm_execute", _blind_endpoint_reads)

        assert resp.status_code == 409, f"竞态应当收敛为 409，实得 {resp.status_code}: {resp.text}"
        assert resp.json()["code"] == "ENDPOINT_SLUG_TAKEN"
