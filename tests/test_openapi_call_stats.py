"""拍板③（2026-10-30）: OpenAPI 调用成功率 = 近 7 天窗口、分母含被拒调用。

判据（用户拍板 + ss-19 §10.2 记的「KPI 说 100% 而明细里有失败」）：
① 成功率口径 = 近 7 天窗口内「成功 / 全部调用」，分母含被拒调用，一桶都不排除；
② 统计与明细同源同窗口：同一个响应、同一份 WHERE，前端 KPI 只读 stats、明细只读 items；
③ 无调用记录 ⇒ success_rate 是 null（前端渲染「未记录」），禁把缺数折成 0 或 100%；
④ 「被拒」词表从 app/ 真实字面量派生：成功/失败判据复用 audit_outcome() 的
   AUDIT_FAILURE_MARKERS（failed/denied/rejected/blocked），拒绝码取
   delivery_service.execute_endpoint 在**创建沙箱会话之前** raise 的那批真实 code。
   这里机检两件事：每个词真实存在，且「被拒 / 执行失败」的分界被源码顺序钉住。
"""
import inspect
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.api import audit as audit_api
from app.api import deliveries as dl
from app.models.api_endpoint import ApiEndpoint
from app.models.audit_log import AuditLog
from app.models.contract import Contract
from app.models.user import User, UserRole
from app.services.auth_service import create_access_token, hash_password
from app.services import delivery_service as dv

REPO = Path(__file__).resolve().parents[1]
NOW = datetime.now(timezone.utc)

# 本文件同步的源码机检与异步的端点用例混在一个模块里：asyncio_mode=auto 已经会把
# async 用例挂上标记，这里再打模块级 pytest.mark.asyncio 只会给同步用例刷一串警告。


async def _parties(db):
    async def _mk(role, prefix):
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

    provider, provider_headers = await _mk(UserRole.DATA_PROVIDER, "ocstatprov")
    buyer, buyer_headers = await _mk(UserRole.BUYER, "ocstatbuy")
    outsider, outsider_headers = await _mk(UserRole.BUYER, "ocstatout")
    return provider, provider_headers, buyer, buyer_headers, outsider, outsider_headers


async def _mk_endpoint(db, provider, buyer):
    contract = Contract(
        contract_no=f"CT-{uuid.uuid4().hex[:12]}",
        contract_type="data_usage",
        status="active",
        provider_id=provider.id,
        buyer_id=buyer.id,
        title="调用统计测试合约",
        product_ids=[str(uuid.uuid4())],
        delivery_methods=["open_api"],
    )
    db.add(contract)
    await db.flush()
    endpoint = ApiEndpoint(
        owner_id=buyer.id,
        contract_id=contract.id,
        name="日报",
        slug=f"stat-{uuid.uuid4().hex[:8]}",
    )
    db.add(endpoint)
    await db.flush()
    return contract, endpoint


def _calls_url(endpoint_id, query=""):
    return f"/api/v1/deliveries/openapi/endpoints/{endpoint_id}/calls{query}"


async def _log(db, endpoint, *, action="openapi.call", code=None, at=None):
    detail = {
        "contract_id": str(endpoint.contract_id),
        "endpoint_id": str(endpoint.id),
        "slug": endpoint.slug,
    }
    if action == "openapi.call":
        detail["output_rows"] = 3
    else:
        detail["code"] = code
        detail["message"] = f"失败：{code}"
    db.add(AuditLog(
        action=action,
        resource_type="api_endpoint",
        resource_id=str(endpoint.id),
        ip_address="203.0.113.9",
        detail=detail,
        created_at=at or NOW,
    ))
    await db.flush()


async def _seed_mixed(db, endpoint):
    """窗口内 4 成功 + 1 准入被拒（配额）+ 1 执行失败（负载挂了）。"""
    recent = NOW - timedelta(hours=6)
    for _ in range(4):
        await _log(db, endpoint, at=recent)
    await _log(db, endpoint, action="openapi.call_failed", code="QUOTA_EXCEEDED", at=recent)
    await _log(db, endpoint, action="openapi.call_failed", code="WORKLOAD_FAILED", at=recent)


class TestStatsCaliber:
    async def test_seven_day_stats_partition_and_caliber_fields(self, client, db_session):
        provider, _ph, buyer, headers, _o, _oh = await _parties(db_session)
        _contract, endpoint = await _mk_endpoint(db_session, provider, buyer)
        await _seed_mixed(db_session, endpoint)

        body = (await client.get(_calls_url(endpoint.id, "?window=7d"), headers=headers)).json()
        stats = body["stats"]

        assert stats["window"] == "7d"
        assert stats["window_days"] == 7
        assert stats["total"] == 6
        # 分母含被拒：QUOTA_EXCEEDED 没进沙箱就算被拒，也照样在分母里
        assert stats["succeeded"] == 4
        assert stats["rejected"] == 1
        assert stats["failed"] == 1
        assert stats["total"] == stats["succeeded"] + stats["rejected"] + stats["failed"]
        assert stats["success_rate"] == pytest.approx(66.7)
        assert stats["data_available"] is True
        assert stats["reason"] is None
        # 口径由后端写死并随响应下发，前端照抄，不许各自解释
        assert "含被拒" in stats["denominator"]
        assert "成功 / 全部调用" in stats["rate_caliber"]
        assert stats["source"]
        assert stats["since"] and stats["until"]
        assert body["window"] == "7d" and body["window_days"] == 7

    async def test_refusals_are_in_the_denominator_not_dropped(self, client, db_session):
        """1 成功 + 3 被拒 ⇒ 25%，不是把被拒剔掉之后的 100%（ss-19 §10.2 的病根）。"""
        provider, _ph, buyer, headers, _o, _oh = await _parties(db_session)
        _contract, endpoint = await _mk_endpoint(db_session, provider, buyer)
        await _log(db_session, endpoint)
        for code in ("RATE_LIMITED", "QUOTA_EXCEEDED", "ENDPOINT_DISABLED"):
            await _log(db_session, endpoint, action="openapi.call_failed", code=code)

        stats = (await client.get(_calls_url(endpoint.id, "?window=7d"), headers=headers)).json()["stats"]
        assert (stats["total"], stats["succeeded"], stats["rejected"]) == (4, 1, 3)
        assert stats["success_rate"] == pytest.approx(25.0)

    async def test_no_calls_reports_null_rate_not_zero_or_hundred(self, client, db_session):
        provider, _ph, buyer, headers, _o, _oh = await _parties(db_session)
        _contract, endpoint = await _mk_endpoint(db_session, provider, buyer)

        body = (await client.get(_calls_url(endpoint.id, "?window=7d"), headers=headers)).json()
        stats = body["stats"]
        assert stats["total"] == 0
        assert stats["success_rate"] is None
        assert stats["data_available"] is False
        assert stats["reason"] == "no_calls_in_window"
        assert stats["succeeded"] == stats["failed"] == stats["rejected"] == 0
        assert body["items"] == [] and body["total"] == 0

    async def test_window_bounds_are_honored_and_disclosed(self, client, db_session):
        provider, _ph, buyer, headers, _o, _oh = await _parties(db_session)
        _contract, endpoint = await _mk_endpoint(db_session, provider, buyer)
        await _log(db_session, endpoint)                        # 窗口内
        await _log(db_session, endpoint)                        # 窗口内
        await _log(db_session, endpoint, at=NOW - timedelta(days=20))   # 窗口外
        await _log(db_session, endpoint, action="openapi.call_failed",
                   code="QUOTA_EXCEEDED", at=NOW - timedelta(days=40))  # 窗口外

        seven = (await client.get(_calls_url(endpoint.id, "?window=7d"), headers=headers)).json()
        assert seven["stats"]["total"] == 2
        assert seven["stats"]["success_rate"] == pytest.approx(100.0)

        whole = (await client.get(_calls_url(endpoint.id, "?window=all"), headers=headers)).json()
        assert whole["stats"]["total"] == 4
        assert whole["window"] == "all" and whole["window_days"] is None
        assert whole["since"] is None
        # 全量口径要照实说是全量，不能仍挂着 7 天的说法
        assert "全部时间" in whole["stats"]["rate_caliber"]

        # 默认值不迁移：SS-18 的明细默认仍是全量（既有断言不被这次改动改掉）
        default = (await client.get(_calls_url(endpoint.id), headers=headers)).json()
        assert default["window"] == "all" and default["total"] == 4

    async def test_bad_window_is_422(self, client, db_session):
        provider, _ph, buyer, headers, _o, _oh = await _parties(db_session)
        _contract, endpoint = await _mk_endpoint(db_session, provider, buyer)
        resp = await client.get(_calls_url(endpoint.id, "?window=2w"), headers=headers)
        assert resp.status_code == 422, resp.text


class TestOneQueryOneCaliber:
    async def test_status_filter_narrows_items_not_stats(self, client, db_session):
        """明细可以只看失败，但成功率说的还是这个窗口的全部调用——否则一勾「失败」就变 0%。"""
        provider, _ph, buyer, headers, _o, _oh = await _parties(db_session)
        _contract, endpoint = await _mk_endpoint(db_session, provider, buyer)
        await _seed_mixed(db_session, endpoint)

        all_body = (await client.get(_calls_url(endpoint.id, "?window=7d"), headers=headers)).json()
        failed_body = (await client.get(
            _calls_url(endpoint.id, "?window=7d&status=failed"), headers=headers)).json()

        assert [i["status"] for i in failed_body["items"]] == ["failed", "failed"]
        assert failed_body["total"] == 2
        # 同窗口两次请求：stats 覆盖的仍是窗口内全部调用，不随明细筛选缩水
        assert failed_body["stats"]["total"] == all_body["stats"]["total"]
        assert failed_body["stats"]["total"] == 6
        assert failed_body["stats"]["success_rate"] == pytest.approx(66.7)
        assert "status" in failed_body["stats"]["status_scope"]

    async def test_detail_outcome_uses_the_same_bucket_as_stats(self, client, db_session):
        provider, _ph, buyer, headers, _o, _oh = await _parties(db_session)
        _contract, endpoint = await _mk_endpoint(db_session, provider, buyer)
        await _seed_mixed(db_session, endpoint)

        body = (await client.get(_calls_url(endpoint.id, "?window=7d&page_size=100"), headers=headers)).json()
        buckets = {}
        for item in body["items"]:
            buckets[item["outcome"]] = buckets.get(item["outcome"], 0) + 1
            assert item["outcome"] in ("succeeded", "failed", "rejected")
        # 明细行按同一判据归类，数出来与 stats 三桶完全一致
        assert buckets == {
            "succeeded": body["stats"]["succeeded"],
            "failed": body["stats"]["failed"],
            "rejected": body["stats"]["rejected"],
        }
        rejected = [i for i in body["items"] if i["outcome"] == "rejected"]
        assert [i["code"] for i in rejected] == ["QUOTA_EXCEEDED"]
        assert [i["code"] for i in body["items"] if i["outcome"] == "failed"] == ["WORKLOAD_FAILED"]


class TestRejectionVocabularyIsReal:
    """拒绝词表不许在端点里凭空发明：每个词都要在 app/ 源码里真的存在，且位置对得上。"""

    def test_rejection_markers_are_derived_not_retyped(self):
        assert dl.OPENAPI_REJECTION_MARKERS == tuple(
            m for m in audit_api.AUDIT_FAILURE_MARKERS if m != "failed"
        )
        assert set(dl.OPENAPI_REJECTION_MARKERS) == {"denied", "rejected", "blocked"}

    def test_audit_failure_markers_exist_in_audit_source(self):
        src = (REPO / "app/api/audit.py").read_text(encoding="utf-8")
        for marker in audit_api.AUDIT_FAILURE_MARKERS:
            assert f'"{marker}"' in src, f"audit.py 里没有 {marker} 这个标记词"

    def test_refusal_codes_are_raised_before_the_sandbox_session(self):
        """每个拒绝码都在「创建沙箱会话之前」抛出 ⇒ 它确实是「没跑起来就被挡下」，不是注释说的。"""
        exec_src = inspect.getsource(dv.execute_endpoint)
        pivot = exec_src.index("create_sandbox_session(")
        # RATE_LIMITED 由准入辅助函数抛出；该函数本身必须在建会话之前被调用
        assert "check_endpoint_rate_limit(" in exec_src[:pivot], "限流辅助函数不在准入阶段被调用"
        admission = exec_src[:pivot] + inspect.getsource(dv.check_endpoint_rate_limit)
        for code in dl.OPENAPI_REFUSAL_CODES:
            assert f'"{code}"' in admission, f"准入阶段没有真实抛出 {code}"

    def test_execution_failure_codes_are_raised_after_the_sandbox_session(self):
        src = inspect.getsource(dv.execute_endpoint)
        pivot = src.index("create_sandbox_session(")
        for code in dl.OPENAPI_EXECUTION_FAILURE_CODES:
            assert f'"{code}"' in src, f"execute_endpoint 里没有真实抛出 {code}"
            assert src.index(f'"{code}"') > pivot, f"{code} 是执行前拒绝，不该算执行失败"

    def test_bucket_split_is_exhaustive_and_disjoint(self):
        # 成功 → succeeded；执行前拒 → rejected；执行后失败 → failed；未知码兜底 failed
        assert dl._call_bucket("openapi.call", None) == "succeeded"
        assert dl._call_bucket("openapi.call_denied", None) == "rejected"
        assert dl._call_bucket("openapi.call_rejected", None) == "rejected"
        assert dl._call_bucket("openapi.call_blocked", None) == "rejected"
        assert dl._call_bucket("openapi.call_failed", "RATE_LIMITED") == "rejected"
        assert dl._call_bucket("openapi.call_failed", "WORKLOAD_FAILED") == "failed"
        assert dl._call_bucket("openapi.call_failed", None) == "failed"
        assert dl._call_bucket("openapi.call_failed", "SOMETHING_NEW") == "failed"

    async def test_stats_echoes_the_vocabulary_it_used(self, client, db_session):
        """词表随响应下发：前端 KPI 说明里写的口径，就是后端算的时候用的那份。"""
        provider, _ph, buyer, headers, _o, _oh = await _parties(db_session)
        _contract, endpoint = await _mk_endpoint(db_session, provider, buyer)
        await _seed_mixed(db_session, endpoint)

        stats = (await client.get(_calls_url(endpoint.id, "?window=7d"), headers=headers)).json()["stats"]
        assert stats["vocabulary"]["actions"] == list(dl.OPENAPI_CALL_ACTIONS)
        assert stats["vocabulary"]["failure_markers"] == list(audit_api.AUDIT_FAILURE_MARKERS)
        assert stats["vocabulary"]["rejection_markers"] == list(dl.OPENAPI_REJECTION_MARKERS)
        assert stats["vocabulary"]["refusal_codes"] == list(dl.OPENAPI_REFUSAL_CODES)
        assert stats["vocabulary"]["execution_failure_codes"] == list(dl.OPENAPI_EXECUTION_FAILURE_CODES)
        assert set(dl.OPENAPI_REFUSAL_CODES).isdisjoint(dl.OPENAPI_EXECUTION_FAILURE_CODES)
