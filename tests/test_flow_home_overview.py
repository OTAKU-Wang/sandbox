"""D-e2e-23: 首页（原型图 1「首页-平台总览」）聚合面 GET /api/v1/flow/dashboard/home 的口径契约。

每条断言都是「首页那个数字/那一列必须怎么算」的可失败判据，不是渲染快照：
1. 开发任务：进行中=未归档、已结束=archived（状态词表只有 draft/archived，app/models/notebook.py:21-24）；
   「最近创建」按 created_at 排，**不是**列表页的 updated_at（app/api/notebooks.py:379）。
2. 结果通道在线/离线 = enabled/disabled，且按合约当事方作用域（buyer 与 provider 看到同一组数）。
3. 今日调用 = audit_logs 里 openapi.call/openapi.call_failed 的当日行数；失败数由动作名派生
   （复用 app/api/audit.py::audit_outcome），跨日行不得混进来。
4. 今日操作日志：非平台角色只能看本人（GET /api/v1/audit/records 对其返回 403，首页不得越权给平台数）。
5. 近 7 天趋势：桶按日升序、最后一桶恒为今天、不得出现未来日期桶；两类序列各数各的动作名，
   且只数当前主体的审计行。
6. 最新结果集：state 三态只从打包时落在行上的 t5/watermark 派生，历史 NULL 行是「未记录」不是「正常」；
   call_count 用合约下接口累计调用数并带口径说明。
7. 内存档：分子是「已分配」（活跃会话 resource_limits 之和），used_mb 恒 null + measured=false，
   绝不在这里谎称实测占用（项目铁律 honest degradation）。
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.api.flow_runtime import (
    CALL_ACTIONS,
    DEBUG_TREND_ACTIONS,
    EXEC_TREND_ACTIONS,
)
from app.models.api_endpoint import ApiEndpoint, ApiEndpointStatus
from app.models.audit_log import AuditLog
from app.models.compute_run import ComputeRun, ComputeRunStatus
from app.models.contract import Contract
from app.models.delivery_bundle import DeliveryBundle
from app.models.notebook import Notebook, NotebookStatus
from app.models.sandbox_session import SandboxMode, SandboxSession, SessionStatus
from app.models.user import User, UserRole
from app.services import sandbox_manager
from app.services.auth_service import create_access_token, hash_password

HOME_URL = "/api/v1/flow/dashboard/home"
BUNDLES_URL = "/api/v1/deliveries/dataset/bundles"

def _now() -> datetime:
    # CR MEDIUM-1：不用模块级常量——模块导入与断言执行可能跨 UTC 午夜，
    # 「今日窗口」类断言会 flake。取值贴近各自 seed，窗口内毫秒级偏差无害。
    return datetime.now(timezone.utc)


async def _mk_user(db, role: str, prefix: str):
    unique = uuid.uuid4().hex[:8]
    username = f"{prefix}_{unique}".replace("-", "_")
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("testpass123"),
        role=role,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user, {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


async def _mk_contract(db, provider, buyer) -> Contract:
    contract = Contract(
        contract_no=f"CT-HOME-{uuid.uuid4().hex[:10]}",
        contract_type="data_usage",
        status="active",
        provider_id=provider.id,
        buyer_id=buyer.id,
        title="首页聚合测试合约",
        product_ids=[str(uuid.uuid4())],
        delivery_methods=["dataset"],
    )
    db.add(contract)
    await db.flush()
    await db.refresh(contract)
    return contract


async def _parties(db):
    """provider + buyer（同一合约）与另一对无关租户（用于证明作用域）。"""
    provider, provider_headers = await _mk_user(db, UserRole.DATA_PROVIDER, "homeprov")
    buyer, buyer_headers = await _mk_user(db, UserRole.BUYER, "homebuy")
    mine = await _mk_contract(db, provider, buyer)
    alien_provider, alien_headers = await _mk_user(db, UserRole.DATA_PROVIDER, "homealien")
    alien = await _mk_contract(db, alien_provider, alien_provider)
    return {
        "provider": provider,
        "buyer": buyer,
        "headers": buyer_headers,
        "provider_headers": provider_headers,
        "contract": mine,
        "alien_provider": alien_provider,
        "alien_contract": alien,
        "alien_headers": alien_headers,
    }


def _as_utc(moment: datetime) -> datetime:
    """sqlite 回读是 naive UTC，PG 回读带 tz——自证断言两边得先对齐才能比。"""
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


async def _mk_nb(db, user, *, created_at, status=NotebookStatus.DRAFT.value, updated_at=None, title=None):
    nb = Notebook(
        user_id=user.id,
        title=title or f"首页任务-{uuid.uuid4().hex[:6]}",
        path=f"/notebooks/{uuid.uuid4()}.ipynb",
        status=status,
        task_no=f"TASK-{uuid.uuid4().hex[:12].upper()}",
        debug_method="jupyter",
        created_at=created_at,
    )
    db.add(nb)
    await db.flush()
    if updated_at is not None:
        nb.updated_at = updated_at
        await db.flush()
    return nb


async def _mk_audit(db, *, action, user_id=None, moment=None, resource_id=None):
    row = AuditLog(
        user_id=user_id,
        action=action,
        resource_type="dashboard_test",
        resource_id=resource_id or uuid.uuid4().hex[:12],
        detail={"case": "flow_home"},
        created_at=moment or _now(),
    )
    db.add(row)
    await db.flush()
    return row


async def _mk_bundle(db, contract, creator, **kw):
    bundle = DeliveryBundle(
        id=uuid.uuid4(),
        contract_id=contract.id,
        created_by=creator.id,
        object_name=f"delivery/{uuid.uuid4()}.zip",
        size_bytes=kw.pop("size_bytes", 2048),
        file_count=kw.pop("file_count", 1),
        **kw,
    )
    db.add(bundle)
    await db.flush()
    return bundle


class TestDevTasks:
    async def test_split_and_created_at_order(self, client, db_session):
        """进行中/已结束按 archived 切；「最近创建」按 created_at，不被 updated_at 劫持。"""
        p = await _parties(db_session)
        old, mid, fresh = _now() - timedelta(days=3), _now() - timedelta(days=2), _now() - timedelta(hours=6)
        # 最老创建的那条故意把 updated_at 推到最新：按 updated_at 排序就会错判成第一行
        nb_oldest = await _mk_nb(db_session, p["buyer"], created_at=old, updated_at=_now() + timedelta(days=1))
        nb_mid = await _mk_nb(db_session, p["buyer"], created_at=mid, status=NotebookStatus.ARCHIVED.value)
        nb_fresh = await _mk_nb(db_session, p["provider"], created_at=fresh)
        await _mk_nb(db_session, p["alien_provider"], created_at=_now())

        res = await client.get(HOME_URL, headers=p["headers"])
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["dev_tasks"]["total"] == 2
        assert body["dev_tasks"]["active"] == 1
        assert body["dev_tasks"]["ended"] == 1
        assert body["task_filters"] == {"all": 2, "active": 1, "ended": 1}
        assert body["dev_tasks"]["status_caliber"]["ended"].endswith(NotebookStatus.ARCHIVED.value)

        listed = body["recent_tasks"]
        # 自证夹具真的把「最老创建」做成了「最近更新」，否则本例退化出不区分两种排序的断言
        assert _as_utc(nb_oldest.updated_at) > _as_utc(nb_mid.updated_at)
        assert [t["task_no"] for t in listed] == [nb_mid.task_no, nb_oldest.task_no]
        assert listed[0]["debug_method"] == "jupyter"
        assert listed[0]["created_at"].startswith(mid.strftime("%Y-%m-%dT%H:%M"))

        # 开发者本人（provider）看自己的任务是另一套数：按 user_id 切，不按合约当事方
        res2 = await client.get(HOME_URL, headers=p["provider_headers"])
        assert res2.json()["dev_tasks"] == {
            "total": 1,
            "active": 1,
            "ended": 0,
            "status_caliber": res2.json()["dev_tasks"]["status_caliber"],
        }
        assert [t["task_no"] for t in res2.json()["recent_tasks"]] == [nb_fresh.task_no]

    async def test_recent_limit_caps_rows(self, client, db_session):
        p = await _parties(db_session)
        for i in range(3):
            await _mk_nb(db_session, p["buyer"], created_at=_now() - timedelta(hours=i + 1))
        res = await client.get(HOME_URL, params={"recent_limit": 2}, headers=p["headers"])
        assert res.status_code == 200, res.text
        body = res.json()
        assert len(body["recent_tasks"]) == 2
        assert body["dev_tasks"]["total"] == 3


class TestExportInterfaces:
    async def test_online_offline_and_party_scope(self, client, db_session):
        p = await _parties(db_session)

        def ep(contract, owner, status, *, name, calls=0, last=None):
            return ApiEndpoint(
                owner_id=owner.id,
                contract_id=contract.id,
                name=name,
                slug=uuid.uuid4().hex[:16],
                status=status,
                calls_used=calls,
                last_called_at=last,
            )

        last_call = _now() - timedelta(minutes=9)
        db_session.add_all(
            [
                ep(p["contract"], p["provider"], ApiEndpointStatus.ENABLED.value, name="在线一", calls=5, last=last_call),
                ep(p["contract"], p["buyer"], ApiEndpointStatus.ENABLED.value, name="在线二", calls=3),
                ep(p["contract"], p["provider"], ApiEndpointStatus.DISABLED.value, name="离线一"),
                ep(p["alien_contract"], p["alien_provider"], ApiEndpointStatus.ENABLED.value, name="别人家的"),
            ]
        )
        await db_session.flush()

        for headers in (p["headers"], p["provider_headers"]):
            res = await client.get(HOME_URL, headers=headers)
            assert res.status_code == 200, res.text
            block = res.json()["export_interfaces"]
            assert block == {
                "total": 3,
                "online": 2,
                "offline": 1,
                "status_caliber": {"online": "enabled", "offline": "disabled"},
            }
            assert res.json()["calls_today"]["last_called_at"] is not None
            assert res.json()["calls_today"]["last_called_at"].startswith(last_call.strftime("%Y-%m-%dT%H:%M"))

        alien = await client.get(HOME_URL, headers=p["alien_headers"])
        assert alien.json()["export_interfaces"]["total"] == 1

    async def test_no_endpoint_means_null_last_called(self, client, db_session):
        p = await _parties(db_session)
        res = await client.get(HOME_URL, headers=p["headers"])
        assert res.json()["calls_today"]["last_called_at"] is None
        assert res.json()["calls_today"]["count"] == 0


class TestTodayCounters:
    async def test_calls_today_counts_failures_and_window(self, client, db_session):
        p = await _parties(db_session)
        uid = p["buyer"].id
        for _ in range(2):
            await _mk_audit(db_session, action="openapi.call", user_id=uid, moment=_now())
        await _mk_audit(db_session, action="openapi.call_failed", user_id=uid, moment=_now())
        await _mk_audit(db_session, action="openapi.call", user_id=uid, moment=_now() - timedelta(days=2))
        await _mk_audit(db_session, action="sandbox.exec_failed", user_id=uid, moment=_now())

        res = await client.get(HOME_URL, headers=p["headers"])
        body = res.json()
        today = body["calls_today"]
        # 今日 3 条（含 1 条失败）；跨日的 1 条与其它动作都不算
        assert today["count"] == 3, today
        assert today["failures"] == 1, today
        assert today["source"] == "audit_logs"
        assert today["window"] == "utc_day"
        assert sorted(today["actions"]) == sorted(CALL_ACTIONS)
        # 本人口径的操作日志 = 当日该账号全部 4 行（跳日那行不计），失败 2 行
        ops = body["ops_log_today"]
        assert ops["count"] == 4, ops
        assert ops["failures"] == 2, ops
        assert ops["visibility"] == "self"

    async def test_ops_log_visibility_self_versus_platform(self, client, db_session):
        """BUYER 只看得到自己；持平台审计角色才给平台数（与 audit/records 的 403 口径不打架）。"""
        p = await _parties(db_session)
        await _mk_audit(db_session, action="delivery.dataset.preview", user_id=p["buyer"].id, moment=_now())
        await _mk_audit(db_session, action="notebook.run", user_id=p["alien_provider"].id, moment=_now())
        _, operator_headers = await _mk_user(db_session, "operator", "homeoper")

        buyer_ops = (await client.get(HOME_URL, headers=p["headers"])).json()["ops_log_today"]
        assert buyer_ops["visibility"] == "self"
        assert buyer_ops["count"] == 1, buyer_ops

        oper_ops = (await client.get(HOME_URL, headers=operator_headers)).json()["ops_log_today"]
        assert oper_ops["visibility"] == "platform"
        assert oper_ops["count"] >= 2, oper_ops

    async def test_empty_day_is_zero_not_missing(self, client, db_session):
        """没有调用就是 0，并且口径字段仍在（前端不得把缺数渲染成 0 之外的东西）。"""
        p = await _parties(db_session)
        today = (await client.get(HOME_URL, headers=p["headers"])).json()["calls_today"]
        assert today["count"] == 0 and today["failures"] == 0
        assert today["source"] == "audit_logs"


class TestTrend:
    async def test_buckets_end_today_and_split_two_series(self, client, db_session):
        p = await _parties(db_session)
        uid = p["buyer"].id
        three_days_ago = _now() - timedelta(days=3)
        await _mk_audit(db_session, action="notebook.cell_executed", user_id=uid, moment=_now())
        await _mk_audit(db_session, action="notebook.run", user_id=uid, moment=_now())
        await _mk_audit(db_session, action="compute.run.requested", user_id=uid, moment=_now())
        await _mk_audit(db_session, action="notebook.run", user_id=uid, moment=three_days_ago)
        # 别人的执行不得混进我的趋势（本页 scope.note 声明「非平台全局汇总」）
        await _mk_audit(db_session, action="compute.run.requested", user_id=p["alien_provider"].id, moment=_now())

        body = (await client.get(HOME_URL, headers=p["headers"])).json()
        days = body["trend"]["days"]
        assert len(days) == 7
        dates = [d["date"] for d in days]
        assert dates == sorted(dates), dates
        assert len(set(dates)) == 7
        assert dates[-1] == _now().strftime("%Y-%m-%d")
        assert dates[-1] >= datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert all(d <= _now().strftime("%Y-%m-%d") for d in dates), "出现未来日期桶"
        assert dates[0] == (_now() - timedelta(days=6)).strftime("%Y-%m-%d")

        assert days[-1] == {"date": dates[-1], "debug": 2, "exec": 1}
        back_three = next(d for d in days if d["date"] == three_days_ago.strftime("%Y-%m-%d"))
        assert back_three["debug"] == 1 and back_three["exec"] == 0
        assert body["trend"]["series"] == {
            "debug": list(DEBUG_TREND_ACTIONS),
            "exec": list(EXEC_TREND_ACTIONS),
        }
        assert "审计行" in body["trend"]["note"]

    async def test_window_days_param(self, client, db_session):
        p = await _parties(db_session)
        body = (await client.get(HOME_URL, params={"days": 3}, headers=p["headers"])).json()
        assert len(body["trend"]["days"]) == 3
        assert body["trend"]["window_days"] == 3


class TestLatestResultSets:
    async def test_state_three_valued_and_call_count_caliber(self, client, db_session):
        p = await _parties(db_session)
        db_session.add_all(
            [
                ApiEndpoint(
                    owner_id=p["provider"].id,
                    contract_id=p["contract"].id,
                    name="接口A",
                    slug=uuid.uuid4().hex[:16],
                    status=ApiEndpointStatus.ENABLED.value,
                    calls_used=5,
                ),
                ApiEndpoint(
                    owner_id=p["provider"].id,
                    contract_id=p["contract"].id,
                    name="接口B",
                    slug=uuid.uuid4().hex[:16],
                    status=ApiEndpointStatus.DISABLED.value,
                    calls_used=3,
                ),
            ]
        )
        await db_session.flush()

        ok = await _mk_bundle(
            db_session, p["contract"], p["buyer"], t5_review_status="passed", watermark_status="applied"
        )
        partial = await _mk_bundle(
            db_session,
            p["contract"],
            p["buyer"],
            t5_review_status="partially_reviewed",
            watermark_status="applied",
        )
        legacy = await _mk_bundle(db_session, p["contract"], p["buyer"])
        await _mk_bundle(db_session, p["alien_contract"], p["alien_provider"], t5_review_status="passed")

        res = await client.get(HOME_URL, headers=p["headers"])
        assert res.status_code == 200, res.text
        body = res.json()
        rows = {r["bundle_id"]: r for r in body["latest_result_sets"]}
        assert len(rows) == 3, "别人合约的包不得出现在我的首页"
        assert rows[str(ok.id)]["state"] == "normal"
        assert rows[str(ok.id)]["state_reason"] is None
        assert rows[str(partial.id)]["state"] == "abnormal"
        assert rows[str(partial.id)]["state_reason"]
        # 历史 NULL 行 = 未记录，既不是 normal 也不是 abnormal
        assert rows[str(legacy.id)]["state"] is None
        assert rows[str(legacy.id)]["state_reason"]
        # 8 = 该合约下两个接口的累计调用数，口径必须随响应下发
        assert rows[str(ok.id)]["call_count"] == 8
        assert "api_endpoints.calls_used" in body["call_count_caliber"]
        # 名称列有真实来源（对象名），不是前端占位
        assert rows[str(ok.id)]["file_name"] == ok.object_name
        assert rows[str(ok.id)]["size_bytes"] == 2048 and rows[str(ok.id)]["file_count"] == 1

    async def test_bundle_without_interface_has_null_call_count(self, client, db_session):
        p = await _parties(db_session)
        bundle = await _mk_bundle(db_session, p["contract"], p["buyer"], t5_review_status="passed")
        row = next(
            r
            for r in (await client.get(HOME_URL, headers=p["headers"])).json()["latest_result_sets"]
            if r["bundle_id"] == str(bundle.id)
        )
        assert row["call_count"] is None, "合约下没有接口时说 0 就是编数"

    async def test_bundles_list_exposes_file_name(self, client, db_session):
        """列表端点同步补 file_name：交付中心/首页的「叫什么名字」不再靠前端拼。"""
        p = await _parties(db_session)
        bundle = await _mk_bundle(db_session, p["contract"], p["buyer"], t5_review_status="passed")
        res = await client.get(BUNDLES_URL, headers=p["headers"])
        assert res.status_code == 200, res.text
        item = next(i for i in res.json()["items"] if i["bundle_id"] == str(bundle.id))
        assert item["file_name"] == bundle.object_name


class TestMemoryPanels:
    async def test_reserved_not_measured_and_mode_split(self, client, db_session):
        p = await _parties(db_session)
        uid = p["buyer"].id

        def session(mode, status, memory_mb):
            return SandboxSession(
                user_id=uid,
                data_product_id=uuid.uuid4(),
                sandbox_mode=mode,
                status=status,
                resource_limits={"cpu_cores": 2, "memory_mb": memory_mb, "disk_mb": 1024},
            )

        db_session.add_all(
            [
                session(SandboxMode.PRODUCT_DEV.value, SessionStatus.RUNNING.value, 2048),
                session(SandboxMode.PRODUCT_DEV.value, SessionStatus.TERMINATED.value, 4096),
                session(SandboxMode.LLM_TRAINING.value, SessionStatus.READY.value, 1024),
            ]
        )
        await db_session.flush()

        mem = (await client.get(HOME_URL, headers=p["headers"])).json()["memory"]
        assert mem["quota_mb"] == sandbox_manager.TENANT_DEFAULTS["max_memory_mb"]
        assert mem["debug"]["reserved_mb"] == 2048, mem["debug"]
        assert mem["debug"]["active_sessions"] == 1
        assert mem["debug"]["state"] == "busy"
        assert mem["exec"]["reserved_mb"] == 1024, mem["exec"]
        # honest degradation：实测值给不出就是 null，并且 measured=false 必须显式下发
        assert mem["debug"]["used_mb"] is None and mem["debug"]["measured"] is False
        assert mem["exec"]["used_mb"] is None and mem["exec"]["measured"] is False
        assert mem["debug"]["per_task_ceiling_mb"] < mem["exec"]["per_task_ceiling_mb"]

    async def test_idle_and_queued_states(self, client, db_session):
        p = await _parties(db_session)
        body = (await client.get(HOME_URL, headers=p["headers"])).json()["memory"]
        assert body["debug"]["state"] == "idle" and body["debug"]["reserved_mb"] == 0
        assert body["exec"]["state"] == "idle"

        db_session.add(
            ComputeRun(
                contract_id=p["contract"].id,
                requested_by=p["buyer"].id,
                status=ComputeRunStatus.PENDING.value,
            )
        )
        await db_session.flush()
        after = (await client.get(HOME_URL, headers=p["headers"])).json()["memory"]
        assert after["exec"]["state"] == "queued"
        assert after["exec"]["pending_runs"] == 1

        db_session.add(
            ComputeRun(
                contract_id=p["alien_contract"].id,
                requested_by=p["alien_provider"].id,
                status=ComputeRunStatus.PENDING.value,
            )
        )
        await db_session.flush()
        same = (await client.get(HOME_URL, headers=p["headers"])).json()["memory"]
        assert same["exec"]["pending_runs"] == 1, "别人合约的运行改变了我的首页数字"

    async def test_missing_quota_is_null_not_zero(self, client, db_session, monkeypatch):
        """配额拿不到时返回 null，不能让 0 冒充「配额为 0」。"""
        p = await _parties(db_session)
        from app.services import sandbox_manager as sm

        monkeypatch.setattr(sm, "get_tenant_quota", lambda _uid: {"max_memory_mb": 0})
        mem = (await client.get(HOME_URL, headers=p["headers"])).json()["memory"]
        assert mem["quota_mb"] is None


class TestHonestyDisclosures:
    async def test_not_collected_covers_every_unmeasured_number(self, client, db_session):
        p = await _parties(db_session)
        body = (await client.get(HOME_URL, headers=p["headers"])).json()
        keys = {item["key"] for item in body["not_collected"]}
        assert keys == {"memory.measured", "memory.exec.quota", "bundle.call_count"}
        for item in body["not_collected"]:
            assert item["label"] and len(item["reason"]) > 20, item

    async def test_scope_declares_subject_not_platform(self, client, db_session):
        p = await _parties(db_session)
        scope = (await client.get(HOME_URL, headers=p["headers"])).json()["scope"]
        assert scope["username"] == p["buyer"].username
        assert scope["role"] == "buyer"
        assert "非平台全局汇总" in scope["note"]

    async def test_requires_authentication(self, client):
        res = await client.get(HOME_URL)
        assert res.status_code == 401
