"""Team management + team-level quota isolation API (`/api/v1/users/teams`).

2026-11-03 sandbox realignment plan Task 4. Coverage contract:
1. team creation with CPU/memory/storage quotas (the quota columns on the team row
   are the isolation carrier — `app/models/team.py`);
2. members + in-team `TeamRole` (four values; an unknown role must 422, never
   silently degrade to viewer);
3. list envelope `{items,total,page,page_size}` — the repo convention that
   `cds-frontend/src/utils/unwrapList.ts` recognises;
4. quota update + member removal;
5. visibility isolation: platform roles (admin/operator) see everything, business
   roles see only teams they belong to — a team is a quota boundary, so letting any
   account enumerate teams leaks other tenants' staffing;
6. audit trail for the mutating operations (`team.*` rows must really land in
   audit_logs — see app/api/AGENTS.md "every mutating endpoint calls audit_service.log").
"""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.audit_log import AuditLog


@pytest.mark.asyncio
async def test_team_crud_and_member_roles(client: AsyncClient, make_user):
    """Plan Task 4 Step 1 verbatim case: create (with quotas) → add member with a role → the member can see its team."""
    admin_headers, admin_id = await make_user(role="admin", prefix="team_adm")
    dev_headers, dev_id = await make_user(role="data_provider", prefix="team_dev")

    resp = await client.post(
        "/api/v1/users/teams",
        json={
            "name": "金融风控创新实验室",
            "org_id": "org-fin-01",
            "description": "跨机构风控建模团队",
            "cpu_quota_cores": 32,
            "memory_quota_gb": 128,
            "storage_quota_gb": 1000,
        },
        headers=admin_headers,
    )
    assert resp.status_code == 201
    team = resp.json()
    team_id = team["id"]
    assert team["cpu_quota_cores"] == 32
    assert team["memory_quota_gb"] == 128
    assert team["storage_quota_gb"] == 1000
    assert team["created_by"] == admin_id
    assert team["status"] == "active"

    resp_mem = await client.post(
        f"/api/v1/users/teams/{team_id}/members",
        json={"user_id": dev_id, "role": "core_developer", "permissions": ["view", "edit", "run"]},
        headers=admin_headers,
    )
    assert resp_mem.status_code == 200
    assert len(resp_mem.json()["members"]) == 1

    resp_list = await client.get("/api/v1/users/teams", headers=dev_headers)
    assert resp_list.status_code == 200
    assert resp_list.json()["total"] >= 1

    detail = await client.get(f"/api/v1/users/teams/{team_id}", headers=dev_headers)
    assert detail.status_code == 200
    member = detail.json()["members"][0]
    assert member["user_id"] == dev_id
    assert member["role"] == "core_developer"
    assert member["permissions"] == ["view", "edit", "run"]
    assert member["username"]


@pytest.mark.asyncio
async def test_team_list_envelope_and_pagination(client: AsyncClient, make_user):
    """List must be `{items,total,page,page_size}` with self-consistent slicing, plus name search."""
    admin_headers, _ = await make_user(role="admin", prefix="team_pg")
    ids = []
    for i in range(3):
        resp = await client.post(
            "/api/v1/users/teams",
            json={"name": f"分页测试团队-{i}", "cpu_quota_cores": 4 + i, "memory_quota_gb": 8, "storage_quota_gb": 10},
            headers=admin_headers,
        )
        assert resp.status_code == 201
        ids.append(resp.json()["id"])

    page1 = await client.get("/api/v1/users/teams", params={"page": 1, "page_size": 2}, headers=admin_headers)
    assert page1.status_code == 200
    body = page1.json()
    assert set(body) == {"items", "total", "page", "page_size"}
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert len(body["items"]) == 2

    page2 = await client.get("/api/v1/users/teams", params={"page": 2, "page_size": 2}, headers=admin_headers)
    assert page2.json()["total"] == 3
    assert len(page2.json()["items"]) == 1

    seen = {item["id"] for item in page1.json()["items"]} | {item["id"] for item in page2.json()["items"]}
    assert seen == set(ids)

    hit = await client.get("/api/v1/users/teams", params={"q": "分页测试团队-1"}, headers=admin_headers)
    assert hit.json()["total"] == 1
    assert hit.json()["items"][0]["id"] == ids[1]


@pytest.mark.asyncio
async def test_team_quota_update_and_member_removal(client: AsyncClient, make_user, db_session):
    """PATCH updates quotas + role_permissions; DELETE removes a member; each leaves an audit row."""
    admin_headers, admin_id = await make_user(role="admin", prefix="team_upd")
    _, member_id = await make_user(role="buyer", prefix="team_upd_m")

    created = await client.post(
        "/api/v1/users/teams",
        json={"name": "配额调整团队", "cpu_quota_cores": 8, "memory_quota_gb": 32, "storage_quota_gb": 200},
        headers=admin_headers,
    )
    team_id = created.json()["id"]

    member = await client.post(
        f"/api/v1/users/teams/{team_id}/members",
        json={"user_id": member_id, "role": "data_analyst"},
        headers=admin_headers,
    )
    assert member.status_code == 200
    assert member.json()["members"][0]["permissions"] == []

    patched = await client.patch(
        f"/api/v1/users/teams/{team_id}",
        json={
            "cpu_quota_cores": 64,
            "memory_quota_gb": 256,
            "storage_quota_gb": 2048,
            "role_permissions": {"data_analyst": ["view", "run"]},
        },
        headers=admin_headers,
    )
    assert patched.status_code == 200
    body = patched.json()
    assert (body["cpu_quota_cores"], body["memory_quota_gb"], body["storage_quota_gb"]) == (64, 256, 2048)
    assert body["role_permissions"]["data_analyst"] == ["view", "run"]
    assert {
        "id", "name", "members", "role_permissions", "created_by", "member_count",
    } <= set(body)

    removed = await client.delete(f"/api/v1/users/teams/{team_id}/members/{member_id}", headers=admin_headers)
    assert removed.status_code == 200
    assert removed.json()["members"] == []

    again = await client.delete(f"/api/v1/users/teams/{team_id}/members/{member_id}", headers=admin_headers)
    assert again.status_code == 404

    # `client` overrides get_db with this very session, so the audit rows written by the
    # requests above are readable here without a second factory (and uncommitted by design).
    rows = (
        await db_session.execute(
            select(AuditLog.action, AuditLog.user_id, AuditLog.resource_id).where(
                AuditLog.resource_type == "team", AuditLog.resource_id == str(team_id)
            )
        )
    ).all()
    assert {"team.create", "team.member.upsert", "team.update", "team.member.remove"} <= {row[0] for row in rows}
    assert {str(row[1]) for row in rows} == {str(admin_id)}


@pytest.mark.asyncio
async def test_team_membership_isolation(client: AsyncClient, make_user):
    """Visibility face of quota isolation: a non-member business account can neither list nor open the team."""
    admin_headers, _ = await make_user(role="admin", prefix="team_iso")
    owner_headers, owner_id = await make_user(role="data_provider", prefix="team_iso_o")
    stranger_headers, _ = await make_user(role="buyer", prefix="team_iso_s")

    created = await client.post(
        "/api/v1/users/teams",
        json={"name": "越权探测团队", "org_id": "org-secret"},
        headers=admin_headers,
    )
    team_id = created.json()["id"]

    assert (await client.get("/api/v1/users/teams", headers=stranger_headers)).json()["total"] == 0
    # 404 rather than 403: a non-member must not be able to probe that the team exists.
    assert (await client.get(f"/api/v1/users/teams/{team_id}", headers=stranger_headers)).status_code == 404

    added = await client.post(
        f"/api/v1/users/teams/{team_id}/members",
        json={"user_id": owner_id, "role": "viewer"},
        headers=admin_headers,
    )
    assert added.status_code == 200

    mine = await client.get("/api/v1/users/teams", headers=owner_headers)
    assert mine.json()["total"] == 1
    assert mine.json()["items"][0]["id"] == team_id
    assert (await client.get(f"/api/v1/users/teams/{team_id}", headers=owner_headers)).status_code == 200
    assert (
        await client.patch(f"/api/v1/users/teams/{team_id}", json={"cpu_quota_cores": 999}, headers=owner_headers)
    ).status_code == 403
    assert (
        await client.delete(f"/api/v1/users/teams/{team_id}/members/{owner_id}", headers=owner_headers)
    ).status_code == 403


@pytest.mark.asyncio
async def test_team_write_rejected_for_business_role_and_unknown_role_422(client: AsyncClient, make_user):
    """Writes are platform-side only; an unknown team role must 422 instead of degrading to viewer."""
    _, member_id = await make_user(role="data_provider", prefix="team_neg")
    buyer_headers, _ = await make_user(role="buyer", prefix="team_neg_b")

    denied = await client.post("/api/v1/users/teams", json={"name": "业务角色建团"}, headers=buyer_headers)
    assert denied.status_code == 403

    admin_headers, _ = await make_user(role="admin", prefix="team_neg_a")
    created = await client.post("/api/v1/users/teams", json={"name": "角色校验团队"}, headers=admin_headers)
    team_id = created.json()["id"]

    bad_role = await client.post(
        f"/api/v1/users/teams/{team_id}/members",
        json={"user_id": member_id, "role": "superuser"},
        headers=admin_headers,
    )
    assert bad_role.status_code == 422

    bad_perm_key = await client.patch(
        f"/api/v1/users/teams/{team_id}", json={"role_permissions": {"root": ["*"]}}, headers=admin_headers
    )
    assert bad_perm_key.status_code == 422

    ghost = uuid.uuid4()
    missing = await client.post(
        f"/api/v1/users/teams/{team_id}/members",
        json={"user_id": str(ghost), "role": "viewer"},
        headers=admin_headers,
    )
    assert missing.status_code == 404

    assert (await client.get(f"/api/v1/users/teams/{uuid.uuid4()}", headers=admin_headers)).status_code == 404
    assert (
        await client.patch(f"/api/v1/users/teams/{uuid.uuid4()}", json={"name": "x"}, headers=admin_headers)
    ).status_code == 404
    assert (
        await client.delete(f"/api/v1/users/teams/{uuid.uuid4()}/members/{ghost}", headers=admin_headers)
    ).status_code == 404


@pytest.mark.asyncio
async def test_team_member_readd_updates_role(client: AsyncClient, make_user):
    """POST /members is upsert: re-adding a member reassigns their in-team role."""
    admin_headers, _ = await make_user(role="admin", prefix="team_upsert")
    _, member_id = await make_user(role="buyer", prefix="team_up_m")

    team_id = (
        await client.post("/api/v1/users/teams", json={"name": "成员改派团队"}, headers=admin_headers)
    ).json()["id"]

    first = await client.post(
        f"/api/v1/users/teams/{team_id}/members",
        json={"user_id": member_id, "role": "viewer", "permissions": ["view"]},
        headers=admin_headers,
    )
    assert len(first.json()["members"]) == 1

    second = await client.post(
        f"/api/v1/users/teams/{team_id}/members",
        json={"user_id": member_id, "role": "team_admin", "permissions": ["view", "edit", "manage"]},
        headers=admin_headers,
    )
    assert second.status_code == 200
    members = second.json()["members"]
    assert len(members) == 1
    assert members[0]["role"] == "team_admin"
    assert members[0]["permissions"] == ["view", "edit", "manage"]

    detail = await client.get(f"/api/v1/users/teams/{team_id}", headers=admin_headers)
    assert detail.json()["member_count"] == 1
