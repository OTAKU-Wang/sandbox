"""SS-17 配额用量只读面（GET /api/v1/deliveries/quota-usage）。

三件被测的事：
1. 聚合的是真实存在的数（会话并发/租户限额/api_endpoints 调用额度），
   且只统计当前主体的数据；
2. honest degradation：计数器后端（Redis / 进程内兜底）如实披露，未采集的数
   返回 null 而不是 0；
3. 只读——读一次不会改变任何计数器。
"""
import uuid

import pytest
from sqlalchemy import select

from app.core.scopes import resolve_group
from app.models.api_endpoint import ApiEndpoint
from app.models.contract import Contract
from app.models.sandbox_session import SandboxSession, SessionStatus
from app.models.user import User, UserRole
from app.services.auth_service import create_access_token, hash_password
from app.services.quota_manager import QuotaType, quota_manager

pytestmark = pytest.mark.asyncio

QUOTA_PATH = "/api/v1/deliveries/quota-usage"


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
        contract_type="data_application",
        status="active",
        provider_id=provider.id,
        buyer_id=buyer.id,
        title="SS-17 配额测试合约",
        product_ids=[str(uuid.uuid4())],
    )
    db.add(contract)
    await db.flush()
    await db.refresh(contract)
    return contract


async def _mk_session(db, user, status: str, level: str = "l2c") -> SandboxSession:
    session = SandboxSession(
        user_id=user.id,
        data_product_id=uuid.uuid4(),
        sandbox_level=level,
        status=status,
    )
    db.add(session)
    await db.flush()
    await db.refresh(session)
    return session


class _FakeRedis:
    """最小 Redis 替身：get 恒返回 None（= 该键无计数），只用于验证后端披露位。"""

    def __init__(self):
        self.gets = 0

    async def get(self, key):
        self.gets += 1
        return None


class TestAuthAndScope:
    async def test_requires_auth(self, client):
        resp = await client.get(QUOTA_PATH)
        assert resp.status_code == 401

    async def test_prefix_maps_to_gateway_scope_group(self):
        """本路径必须在 SS-12 前缀映射内：未映射前缀对 API Key 默认拒绝，
        配额页的机器主体读取会整片 403。"""
        assert resolve_group(QUOTA_PATH) == "gateway"


class TestEmptyAccount:
    async def test_shape_without_any_data(self, client, db_session):
        user, headers = await _mk_user(db_session, UserRole.DATA_PROVIDER, "ss17empty")

        resp = await client.get(QUOTA_PATH, headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["scope"]["user_id"] == str(user.id)
        sessions = body["sandbox_sessions"]
        assert sessions["total"] == 0
        assert sessions["active"] == 0
        assert sessions["by_status"] == {}
        assert sessions["per_session"] == []
        # 并发上限是创建路径的同源常量，不是「未采集」
        assert sessions["active_limit"] == 5
        assert sessions["active_remaining"] == 5

        openapi = body["openapi_endpoints"]
        assert openapi["count"] == 0
        assert openapi["items"] == []
        # 没有任何端点设置总额度 ⇒ 后端确实没有「总配额」这个数，不能编一个 0
        assert openapi["totals"]["calls_used"] == 0
        assert openapi["totals"]["quota_total"] is None
        assert openapi["totals"]["calls_remaining"] is None

        tenant = body["tenant_resources"]
        assert tenant["limits"] == {
            "cpu_cores": 8,
            "memory_mb": 8192,
            "disk_mb": 20480,
            "max_sessions": 5,
        }
        # 从未有会话占用过资源 ⇒ 用量未采集，而不是 0
        assert tenant["used"] is None

        assert body["not_collected"], "缺口必须点名，不能静默省略"


class TestHonestDegradation:
    async def test_counter_backend_disclosed_as_in_memory(self, client, db_session, monkeypatch):
        """测试环境不连 Redis：披露位必须是 in_memory 并说明不可信边界。"""
        monkeypatch.setattr(quota_manager, "_redis", None)
        _, headers = await _mk_user(db_session, UserRole.DATA_PROVIDER, "ss17mem")

        body = (await client.get(QUOTA_PATH, headers=headers)).json()
        src = body["sources"]["sandbox_counters"]
        assert src["backend"] == "in_memory"
        assert src["cross_replica"] is False
        assert src["survives_restart"] is False

    async def test_counter_backend_disclosed_as_redis(self, client, db_session, monkeypatch):
        """接上 Redis 后披露位翻转；无计数键 ⇒ 不得凭空造数。"""
        fake = _FakeRedis()
        monkeypatch.setattr(quota_manager, "_redis", fake)
        _, headers = await _mk_user(db_session, UserRole.DATA_PROVIDER, "ss17redis")

        body = (await client.get(QUOTA_PATH, headers=headers)).json()
        src = body["sources"]["sandbox_counters"]
        assert src["backend"] == "redis"
        assert src["cross_replica"] is True
        assert src["survives_restart"] is True
        assert fake.gets == 0  # 无活跃会话 ⇒ 一个计数键都不该读


class TestSandboxSessions:
    async def test_counts_and_concurrency_headroom(self, client, db_session):
        user, headers = await _mk_user(db_session, UserRole.BUYER, "ss17cnt")
        for status in (
            SessionStatus.RUNNING.value,
            SessionStatus.PROVISIONING.value,
            SessionStatus.READY.value,
            SessionStatus.COMPLETED.value,
            SessionStatus.TERMINATED.value,
        ):
            await _mk_session(db_session, user, status)

        body = (await client.get(QUOTA_PATH, headers=headers)).json()
        sessions = body["sandbox_sessions"]
        assert sessions["total"] == 5
        assert sessions["active"] == 3
        assert sessions["active_limit"] == 5
        assert sessions["active_remaining"] == 2
        assert sessions["by_status"][SessionStatus.RUNNING.value] == 1
        assert sessions["by_status"][SessionStatus.COMPLETED.value] == 1

    async def test_historic_level_still_reported(self, client, db_session):
        """SS-13 前创建的 L3 会话必须照常可读，不能因级别收敛而报错。"""
        user, headers = await _mk_user(db_session, UserRole.BUYER, "ss17hist")
        await _mk_session(db_session, user, SessionStatus.RUNNING.value, level="L3")

        body = (await client.get(QUOTA_PATH, headers=headers)).json()
        per = body["sandbox_sessions"]["per_session"]
        assert len(per) == 1
        assert per[0]["sandbox_level"] == "L3"

    async def test_per_session_counters_and_unlimited_is_null(self, client, db_session, monkeypatch):
        monkeypatch.setattr(quota_manager, "_redis", None)
        user, headers = await _mk_user(db_session, UserRole.BUYER, "ss17ctr")
        session = await _mk_session(db_session, user, SessionStatus.RUNNING.value)

        await quota_manager.set_limits(session.id, {QuotaType.ROWS: 10000})
        await quota_manager.check_and_increment(session.id, QuotaType.ROWS, 500)

        body = (await client.get(QUOTA_PATH, headers=headers)).json()
        quotas = {q["type"]: q for q in body["sandbox_sessions"]["per_session"][0]["quotas"]}

        assert quotas[QuotaType.ROWS.value]["usage"] == 500
        assert quotas[QuotaType.ROWS.value]["limit"] == 10000
        # dp_epsilon 计数单位是 ε×1e-4，单位必须随数一起给前端
        assert quotas[QuotaType.DP_EPSILON.value]["unit"] == "epsilon×1e-4"
        # 未设限（quota_manager 里 limit 0 == 不限）⇒ null，不能显示成「额度 0」
        assert quotas[QuotaType.BYTES.value]["limit"] is None


class TestOpenApiEndpoints:
    async def test_quota_aggregation_and_visibility(self, client, db_session):
        provider, provider_headers = await _mk_user(db_session, UserRole.DATA_PROVIDER, "ss17own")
        buyer, buyer_headers = await _mk_user(db_session, UserRole.BUYER, "ss17buy")
        stranger, stranger_headers = await _mk_user(db_session, UserRole.DATA_PROVIDER, "ss17out")
        contract = await _mk_contract(db_session, provider, buyer)

        db_session.add_all([
            ApiEndpoint(owner_id=provider.id, contract_id=contract.id, name="限额端点",
                        slug=f"ss17-lim-{uuid.uuid4().hex[:8]}", quota_total=1000, calls_used=137),
            ApiEndpoint(owner_id=provider.id, contract_id=contract.id, name="不限端点",
                        slug=f"ss17-free-{uuid.uuid4().hex[:8]}", calls_used=3),
            ApiEndpoint(owner_id=stranger.id, contract_id=contract.id, name="他人端点",
                        slug=f"ss17-other-{uuid.uuid4().hex[:8]}"),
        ])
        await db_session.flush()

        owner_body = (await client.get(QUOTA_PATH, headers=provider_headers)).json()
        openapi = owner_body["openapi_endpoints"]
        # 可见性与成果包列表同源：合约当事方看到该合约下的全部 OpenAPI 端点（endpoint 挂在
        # 合约交付面上），owner_id 只决定非当事方能否看到自己建的端点。
        assert openapi["count"] == 3
        by_name = {ep["name"]: ep for ep in openapi["items"]}
        assert by_name["限额端点"]["calls_remaining"] == 863
        assert by_name["限额端点"]["role"] == "provider"
        assert by_name["他人端点"]["role"] == "provider"
        # 未设总额度 ⇒ null，而不是「剩 0」
        assert by_name["不限端点"]["quota_total"] is None
        assert by_name["不限端点"]["calls_remaining"] is None
        assert openapi["totals"] == {"calls_used": 140, "quota_total": 1000, "calls_remaining": 860}
        assert openapi["status_enabled"] == 3

        # 合约买方看同一批端点（成果的消费方也要能看额度），角色标为 consumer
        buyer_body = (await client.get(QUOTA_PATH, headers=buyer_headers)).json()
        buyer_openapi = buyer_body["openapi_endpoints"]
        assert buyer_openapi["count"] == 3
        assert {ep["role"] for ep in buyer_openapi["items"]} == {"consumer"}

        # 与合约无关系的账号看不到别人的额度
        other_body = (await client.get(QUOTA_PATH, headers=stranger_headers)).json()
        assert {ep["name"] for ep in other_body["openapi_endpoints"]["items"]} == {"他人端点"}
        assert {ep["role"] for ep in other_body["openapi_endpoints"]["items"]} == {"consumer"}

    async def test_totals_null_when_no_quota_set(self, client, db_session):
        user, headers = await _mk_user(db_session, UserRole.DATA_PROVIDER, "ss17nq")
        other = await _mk_user(db_session, UserRole.BUYER, "ss17nqb")
        contract = await _mk_contract(db_session, user, other[0])
        db_session.add(ApiEndpoint(owner_id=user.id, contract_id=contract.id, name="无额度",
                                   slug=f"ss17-nq-{uuid.uuid4().hex[:8]}", calls_used=9))
        await db_session.flush()

        totals = (await client.get(QUOTA_PATH, headers=headers)).json()["openapi_endpoints"]["totals"]
        assert totals["calls_used"] == 9
        assert totals["quota_total"] is None
        assert totals["calls_remaining"] is None


class TestReadOnly:
    async def test_reading_does_not_change_counters(self, client, db_session, monkeypatch):
        monkeypatch.setattr(quota_manager, "_redis", None)
        user, headers = await _mk_user(db_session, UserRole.BUYER, "ss17ro")
        session = await _mk_session(db_session, user, SessionStatus.RUNNING.value)
        await quota_manager.set_limits(session.id, {QuotaType.API_CALLS: 100})
        await quota_manager.check_and_increment(session.id, QuotaType.API_CALLS, 5)

        first = (await client.get(QUOTA_PATH, headers=headers)).json()
        second = (await client.get(QUOTA_PATH, headers=headers)).json()

        def calls(body):
            return {q["type"]: q["usage"] for q in body["sandbox_sessions"]["per_session"][0]["quotas"]}

        assert calls(first) == calls(second) == {
            qt.value: (5 if qt is QuotaType.API_CALLS else 0) for qt in QuotaType
        }
        assert first["openapi_endpoints"]["count"] == second["openapi_endpoints"]["count"]
        assert first["sandbox_sessions"]["total"] == second["sandbox_sessions"]["total"]


async def test_quota_table_rows_untouched_by_read(client, db_session):
    """GET 不写 api_endpoints（calls_used 只由结果通道调用递增）。"""
    user, headers = await _mk_user(db_session, UserRole.DATA_PROVIDER, "ss17tbl")
    other = await _mk_user(db_session, UserRole.BUYER, "ss17tblb")
    contract = await _mk_contract(db_session, user, other[0])
    endpoint = ApiEndpoint(owner_id=user.id, contract_id=contract.id, name="计数",
                           slug=f"ss17-tbl-{uuid.uuid4().hex[:8]}", calls_used=11)
    db_session.add(endpoint)
    await db_session.flush()

    await client.get(QUOTA_PATH, headers=headers)

    fresh = (await db_session.execute(select(ApiEndpoint).where(ApiEndpoint.id == endpoint.id))).scalar_one()
    assert fresh.calls_used == 11
