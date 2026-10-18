"""SS-19 D-e2e-19 审计查询能力（4-1 操作日志屏的筛选四件套 + 操作结果派生口径）。

覆盖：
- audit_outcome：结果只能由动作名派生，且失败标记集合必须与 app/ 里真实写出的动作名对得上
- AUDIT_EVENT_TYPES：类型页签只能指向代码里真正会写入的动作名（禁伪造：不许为凑页签造名字）
- GET /audit/records 的 date_from / date_to / keyword / outcome / event_type 五个新筛选参数
- 首页 KPI「今日操作日志」与 4-1「共 N 条记录」必须同源：同一次 date_from 查询的 total
"""
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.audit import AUDIT_EVENT_TYPES, AUDIT_FAILURE_MARKERS, audit_event_type, audit_outcome
from app.models.audit_log import AuditLog
from app.models.user import User
from app.services.auth_service import create_access_token, hash_password

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
_ACTION_LITERAL = re.compile(r"""action\s*=\s*(?:f|r)?"([a-z0-9_.]+)\"""")


def _code_action_vocabulary() -> set[str]:
    """app/ 里所有 audit_service.log(action="…") 写出的动作名。"""
    found: set[str] = set()
    for path in APP_ROOT.rglob("*.py"):
        found.update(_ACTION_LITERAL.findall(path.read_text(encoding="utf-8", errors="ignore")))
    return found


async def _operator(db_session: AsyncSession) -> tuple[dict, User]:
    unique = uuid.uuid4().hex[:8]
    user = User(
        username=f"audit_q_{unique}",
        email=f"audit_q_{unique}@example.com",
        hashed_password=hash_password("testpass123"),
        role="operator",
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}, user


async def _seed(
    db_session: AsyncSession,
    *,
    action: str,
    user: User | None = None,
    resource_id: str | None = None,
    resource_type_override: str | None = None,
    created_at: datetime | None = None,
) -> None:
    row = AuditLog(
        user_id=user.id if user else None,
        action=action,
        resource_type=resource_type_override or "audit_query_fixture",
        resource_id=resource_id,
        detail={"fixture": "d-e2e-19"},
        created_at=created_at or datetime.now(timezone.utc),
    )
    db_session.add(row)
    await db_session.flush()


# ─── 派生口径：能失败的两条 ──────────────────────────────


def test_outcome_marker_set_covers_real_failure_actions():
    """每个失败标记都必须在真实动作词表里出现过，否则规则会静默腐烂。"""
    vocabulary = _code_action_vocabulary()
    assert len(vocabulary) >= 30, f"扫描 app/ 只取到 {len(vocabulary)} 个动作名，扫描方式已失效"
    for marker in AUDIT_FAILURE_MARKERS:
        assert any(marker in action for action in vocabulary), f"标记 {marker} 在代码词表里没有任何动作可对应"
    for action in vocabulary:
        expected = "failure" if any(m in action for m in AUDIT_FAILURE_MARKERS) else "success"
        assert audit_outcome(action) == expected, f"{action} 的结果派生与规则不一致"
    assert audit_outcome("openapi.call") == "success"
    assert audit_outcome("openapi.call_failed") == "failure"


def test_event_type_tabs_point_only_at_actions_code_writes():
    """类型页签指向的动作名必须真被代码写入；且每个页签至少一条，否则前端应隐藏该页签。"""
    vocabulary = _code_action_vocabulary()
    assert set(AUDIT_EVENT_TYPES) == {"dev_sandbox", "production", "export", "call"}
    for event_type, actions in AUDIT_EVENT_TYPES.items():
        assert actions, f"页签 {event_type} 没有对应动作，前端不该显示它"
        for action in actions:
            assert action in vocabulary, f"页签 {event_type} 指向了代码不会写入的动作 {action}"
            assert audit_event_type(action) == event_type
    assert audit_event_type("user.login") is None


# ─── 接口参数：不实现就会红的四条 ──────────────────────────


@pytest.mark.asyncio
async def test_records_date_range(client: AsyncClient, db_session: AsyncSession):
    headers, user = await _operator(db_session)
    now = datetime.now(timezone.utc)
    await _seed(db_session, action="openapi.call", user=user, resource_id="res-old", created_at=now - timedelta(days=10))
    await _seed(db_session, action="openapi.call", user=user, resource_id="res-new", created_at=now)

    resp = await client.get(
        "/api/v1/audit/records",
        params={"date_from": (now - timedelta(hours=1)).isoformat(), "limit": 100},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    ids = {item["resource_id"] for item in body["items"]}
    assert "res-new" in ids and "res-old" not in ids
    assert body["total"] == len(body["items"]) >= 1


@pytest.mark.asyncio
async def test_records_keyword_matches_operator_and_object(client: AsyncClient, db_session: AsyncSession):
    headers, user = await _operator(db_session)
    _, other = await _operator(db_session)
    await _seed(db_session, action="openapi.call", user=user, resource_id="kw-target-77")
    await _seed(db_session, action="openapi.call", user=other, resource_id="kw-noise-77")

    by_operator = await client.get("/api/v1/audit/records", params={"keyword": user.username, "limit": 100}, headers=headers)
    assert by_operator.status_code == 200
    items = by_operator.json()["items"]
    assert items and all(item["user_id"] == str(user.id) for item in items)
    assert items[0]["username"] == user.username

    by_object = await client.get("/api/v1/audit/records", params={"keyword": "kw-target-77", "limit": 100}, headers=headers)
    only_target = by_object.json()["items"]
    assert [item["resource_id"] for item in only_target] == ["kw-target-77"]
    assert by_operator.json()["total"] >= 1 and by_object.json()["total"] == 1


@pytest.mark.asyncio
async def test_records_outcome_filter_splits_actions(client: AsyncClient, db_session: AsyncSession):
    headers, user = await _operator(db_session)
    await _seed(db_session, action="openapi.call", user=user, resource_id="ok-1")
    await _seed(db_session, action="openapi.call_failed", user=user, resource_id="bad-1")

    failed = await client.get("/api/v1/audit/records", params={"outcome": "failure", "limit": 100}, headers=headers)
    assert failed.status_code == 200
    failed_items = failed.json()["items"]
    assert [item["resource_id"] for item in failed_items] == ["bad-1"]
    assert all(item["outcome"] == "failure" for item in failed_items)

    ok = await client.get("/api/v1/audit/records", params={"outcome": "success", "limit": 100}, headers=headers)
    ok_ids = {item["resource_id"] for item in ok.json()["items"]}
    assert "ok-1" in ok_ids and "bad-1" not in ok_ids
    assert all(item["outcome"] == "success" for item in ok.json()["items"])


@pytest.mark.asyncio
async def test_records_event_type_tab_and_unknown_value(client: AsyncClient, db_session: AsyncSession):
    headers, user = await _operator(db_session)
    await _seed(db_session, action="delivery.dataset.bundled", user=user, resource_id="bundle-1")
    await _seed(db_session, action="openapi.call", user=user, resource_id="call-1")

    exported = await client.get("/api/v1/audit/records", params={"event_type": "export", "limit": 100}, headers=headers)
    assert exported.status_code == 200
    items = exported.json()["items"]
    assert {item["resource_id"] for item in items} == {"bundle-1"}
    assert all(item["event_type"] == "export" for item in items)

    bad = await client.get("/api/v1/audit/records", params={"event_type": "made_up"}, headers=headers)
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_detail_endpoint_resolves_operator_contract_and_display_no(client: AsyncClient, db_session: AsyncSession):
    """4-2 详情弹窗的「日志编号 / 操作人 / 关联合约 / 操作结果」全部来自后端真值。"""
    from app.models.contract import Contract

    headers, user = await _operator(db_session)
    _, other = await _operator(db_session)
    contract = Contract(
        contract_no=f"CDS-Q-{uuid.uuid4().hex[:8].upper()}",
        contract_type="data_usage",
        provider_id=user.id,
        buyer_id=other.id,
        title="审计详情用例合约",
        product_ids=[],
    )
    db_session.add(contract)
    await db_session.flush()
    await _seed(db_session, action="compute.run.requested", user=user, resource_type_override="contract", resource_id=str(contract.id))

    listed = await client.get("/api/v1/audit/records", params={"event_type": "production", "limit": 10}, headers=headers)
    assert listed.status_code == 200
    row = next(item for item in listed.json()["items"] if item["resource_id"] == str(contract.id))
    assert row["outcome"] == "success" and row["event_type"] == "production"
    assert row["username"] == user.username and row["role"] == "operator"
    assert re.fullmatch(r"LOG-\d{8}-\d{6}-[0-9a-f]{4}", row["display_no"]), row["display_no"]

    detail = await client.get(f"/api/v1/audit/records/{row['id']}", headers=headers)
    assert detail.status_code == 200
    body = detail.json()
    assert body["display_no"] == row["display_no"]
    assert body["username"] == user.username
    assert body["related_contract"] == {"contract_no": contract.contract_no, "title": "审计详情用例合约"}


@pytest.mark.asyncio
async def test_detail_endpoint_without_contract_keeps_it_null(client: AsyncClient, db_session: AsyncSession):
    """合约已被删 / 根本不是合约行：related_contract 给 None，不得拼一个看着像真的名字。"""
    headers, user = await _operator(db_session)
    await _seed(db_session, action="user.login", user=user, resource_id="not-a-uuid")
    listed = await client.get("/api/v1/audit/records", params={"keyword": "not-a-uuid", "limit": 5}, headers=headers)
    row = listed.json()["items"][0]
    detail = await client.get(f"/api/v1/audit/records/{row['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["related_contract"] is None
    assert detail.json()["outcome"] == "success"

@pytest.mark.asyncio
async def test_today_kpi_shares_source_with_list_total(client: AsyncClient, db_session: AsyncSession):
    """首页 KPI 与 4-1 分页条同源：同一个 date_from 查询，total 就是两处显示的那个数。"""
    headers, user = await _operator(db_session)
    now = datetime.now(timezone.utc)
    for idx in range(3):
        await _seed(db_session, action="user.login", user=user, resource_id=f"today-{idx}", created_at=now)
    await _seed(db_session, action="user.login", user=user, resource_id="yesterday", created_at=now - timedelta(days=1))

    start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    params = {"date_from": start_of_today.isoformat(), "limit": 1}
    kpi = await client.get("/api/v1/audit/records", params=params, headers=headers)
    assert kpi.status_code == 200
    today_total = kpi.json()["total"]

    paged = await client.get(
        "/api/v1/audit/records",
        params={"date_from": start_of_today.isoformat(), "limit": 100},
        headers=headers,
    )
    body = paged.json()
    assert today_total == body["total"] == len(body["items"])
    assert "yesterday" not in {item["resource_id"] for item in body["items"]}
    assert today_total >= 3
