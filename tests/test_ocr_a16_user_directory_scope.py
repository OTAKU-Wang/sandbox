"""A16: `GET /users/options` 不得把平台侧账号名册交给普通业务用户。

台账：`GET /users/options` 只有 `require_human`（仅挡 API key 主体），任意登录用户
可按 `role` 拉全平台活跃用户的 用户名+组织+角色（含管理员），为定向社工/找靶提供名册。

修法口径（保持既有产品流程可用）：数商创建合约必须能按 `role=buyer` 选交易对手
（`ContractCreate.tsx` 就调这个），所以交易对手角色（buyer / data_provider）对业务
主体保持可枚举；平台侧角色（operator / regulator / admin）的名册只对平台侧角色开放
——既不能默认列出，也不能显式按该角色筛选，搜索路径同样不得命中。

被测面是 HTTP 端点本身（含 router 级 `require_human` 与查询构造），不是内部 helper。
"""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserRole
from app.services.auth_service import create_access_token, hash_password


async def _mk(db: AsyncSession, role: str, organization: str) -> tuple[dict[str, str], str]:
    username = "a16_" + uuid.uuid4().hex[:10]
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("testpass123"),
        role=role,
        organization=organization,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    token = create_access_token(user.id, user.role)
    return {"Authorization": f"Bearer {token}"}, username


async def _seed(db: AsyncSession) -> dict[str, tuple[dict[str, str], str]]:
    out = {}
    for role, org in (
        (UserRole.ADMIN, "OrgAdmin"),
        (UserRole.REGULATOR, "OrgRegulator"),
        (UserRole.OPERATOR, "OrgOperator"),
        (UserRole.DATA_PROVIDER, "OrgProvider"),
        (UserRole.BUYER, "OrgBuyer"),
        (UserRole.BUYER, "OrgBuyer2"),
    ):
        headers, username = await _mk(db, role, org)
        out[role] = (headers, username)
    headers, username = await _mk(db, UserRole.DATA_PROVIDER, "OrgProvider2")
    out["extra_provider"] = (headers, username)
    return out


async def _names(client: AsyncClient, headers: dict[str, str], **params: int | str) -> list[dict[str, object]]:
    resp = await client.get("/api/v1/users/options", headers=headers, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_privileged_caller_sees_full_roster(client, db_session):
    """运营/监管/管理员的名册用途（用户管理页、运维视图）不得被这次收紧打断。"""
    roster = await _seed(db_session)
    admin_headers = roster[UserRole.ADMIN][0]
    names = {u["username"] for u in await _names(client, admin_headers, limit=100)}
    for role in (
        UserRole.ADMIN,
        UserRole.REGULATOR,
        UserRole.OPERATOR,
        UserRole.DATA_PROVIDER,
        UserRole.BUYER,
    ):
        assert roster[role][1] in names
    by_role = await _names(client, admin_headers, role=UserRole.REGULATOR, limit=100)
    assert [u["username"] for u in by_role] == [roster[UserRole.REGULATOR][1]]


@pytest.mark.parametrize("caller_role", [UserRole.BUYER, UserRole.DATA_PROVIDER])
async def test_business_caller_cannot_enumerate_platform_accounts(client, db_session, caller_role):
    """默认列举（无 role 过滤）不得泄露 operator/regulator/admin 的用户名+组织+角色。"""
    roster = await _seed(db_session)
    caller_headers = roster[caller_role][0]
    body = await _names(client, caller_headers, limit=100)
    names = {u["username"] for u in body}
    for role in (UserRole.ADMIN, UserRole.REGULATOR, UserRole.OPERATOR):
        assert roster[role][1] not in names, f"{role} 账号出现在业务用户的名册里"
    assert {u["role"] for u in body} <= {UserRole.BUYER, UserRole.DATA_PROVIDER}
    assert roster[caller_role][1] in names


@pytest.mark.parametrize("caller_role", [UserRole.BUYER, UserRole.DATA_PROVIDER])
@pytest.mark.parametrize("target_role", [UserRole.ADMIN, UserRole.REGULATOR, UserRole.OPERATOR])
async def test_business_caller_cannot_filter_by_platform_role(client, db_session, caller_role, target_role):
    """`?role=admin` 这类显式筛选必须整条拒绝（403），而不是回空表蒙混。"""
    roster = await _seed(db_session)
    resp = await client.get(
        "/api/v1/users/options",
        headers=roster[caller_role][0],
        params={"role": target_role, "limit": 100},
    )
    assert resp.status_code == 403, resp.text


async def test_business_caller_search_cannot_reach_platform_accounts(client, db_session):
    """搜索路径（q）与默认路径必须同一口径：按管理员用户名子串检索也搜不到。"""
    roster = await _seed(db_session)
    admin_username = roster[UserRole.ADMIN][1]
    caller_headers = roster[UserRole.BUYER][0]
    body = await _names(client, caller_headers, q=admin_username[:12], limit=100)
    assert admin_username not in {u["username"] for u in body}


async def test_provider_can_still_pick_a_buyer_for_contract(client, db_session):
    """产品流程守卫：数商建合约要按 role=buyer 选买方（前端本地过滤，无服务端 q）。"""
    roster = await _seed(db_session)
    body = await _names(client, roster[UserRole.DATA_PROVIDER][0], role=UserRole.BUYER, limit=100)
    names = {u["username"] for u in body}
    assert roster[UserRole.BUYER][1] in names
    assert roster["extra_provider"][1] not in names


async def test_invalid_role_still_400_not_403(client, db_session):
    """自由文本角色仍是 400（参数错误），收紧不得把它变成鉴权错误。"""
    roster = await _seed(db_session)
    resp = await client.get(
        "/api/v1/users/options",
        headers=roster[UserRole.BUYER][0],
        params={"role": "not-a-role"},
    )
    assert resp.status_code == 400, resp.text
