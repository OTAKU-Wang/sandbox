"""场景中心 API (`/api/v1/flow/scenarios`) — 2026-11-03 sandbox realignment Task 5.

覆盖计划要求的四段：创建、列表筛选、详情、承接转项目；外加承接的团队边界
（403 非成员 / 422 多团队不猜 / 409 重复承接）与统一分页信封、审计动作。
"""
import uuid

import pytest
from httpx import AsyncClient

from app.main import app as fastapi_app


async def _create(client: AsyncClient, headers, **overrides):
    body = {
        "title": "医疗影像科研协作场景",
        "industry": "医疗",
        "business_background": "三家医院希望联合训练肺结节模型，原始影像不得离开院内。",
        "target_description": "输出训练好的模型权重与评估报告，影像明细不得导出。",
        "delivery_scene": "tee_compute",
        "suggested_resource_ids": [str(uuid.uuid4())],
        "compliance_constraints": {"output_fields": "model_weights,metrics", "export_approval": True},
        "delivery_tech_config": {"tee_image": "tee-cuda:12.4", "instance_flavor": "gcgn5v", "endpoint": "/model"},
    }
    body.update(overrides)
    resp = await client.post("/api/v1/flow/scenarios", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _team(client: AsyncClient, admin_headers, name: str, member_ids: list[str]) -> str:
    resp = await client.post("/api/v1/users/teams", json={"name": name}, headers=admin_headers)
    assert resp.status_code == 201, resp.text
    team_id = resp.json()["id"]
    for user_id in member_ids:
        added = await client.post(
            f"/api/v1/users/teams/{team_id}/members",
            json={"user_id": user_id},
            headers=admin_headers,
        )
        assert added.status_code == 200, added.text
    return team_id


@pytest.mark.asyncio
async def test_create_scenario_returns_201_and_persists_fields(client: AsyncClient, auth_headers):
    scenario = await _create(client, auth_headers)

    assert scenario["id"]
    assert scenario["title"] == "医疗影像科研协作场景"
    assert scenario["industry"] == "医疗"
    assert scenario["delivery_scene"] == "tee_compute"
    assert scenario["status"] == "published"
    assert scenario["accepted_by"] is None
    assert scenario["accepted_team_id"] is None
    assert scenario["project"] is None
    assert scenario["compliance_constraints"]["output_fields"] == "model_weights,metrics"
    assert scenario["delivery_tech_config"]["tee_image"] == "tee-cuda:12.4"
    assert len(scenario["suggested_resource_ids"]) == 1


@pytest.mark.asyncio
async def test_create_scenario_validates_body(client: AsyncClient, auth_headers):
    assert (await client.post("/api/v1/flow/scenarios", json={"title": ""}, headers=auth_headers)).status_code == 422
    resp = await client.post(
        "/api/v1/flow/scenarios",
        json={"title": "非法交付场景", "delivery_scene": "ftp"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_scenario_surface_requires_authentication(client: AsyncClient):
    assert (await client.get("/api/v1/flow/scenarios")).status_code in (401, 403)
    assert (await client.post("/api/v1/flow/scenarios", json={"title": "匿名场景"})).status_code in (401, 403)


@pytest.mark.asyncio
async def test_list_scenarios_envelope_and_filters(client: AsyncClient, auth_headers):
    created = await _create(client, auth_headers)
    await _create(client, auth_headers, title="零售客群画像场景", industry="零售", delivery_scene="dataset_file")
    await _create(client, auth_headers, title="金融风控联合建模", industry="金融", delivery_scene="api")

    plain = await client.get("/api/v1/flow/scenarios", headers=auth_headers)
    assert plain.status_code == 200
    body = plain.json()
    assert set(body) == {"items", "total", "page", "page_size"}
    assert body["page"] == 1 and body["page_size"] == 20
    assert body["total"] >= 3
    assert {item["id"] for item in body["items"]} >= {created["id"]}

    by_scene = await client.get(
        "/api/v1/flow/scenarios", params={"delivery_scene": "tee_compute"}, headers=auth_headers
    )
    scenes = {item["delivery_scene"] for item in by_scene.json()["items"]}
    assert scenes == {"tee_compute"}
    assert by_scene.json()["total"] >= 1

    by_industry = await client.get(
        "/api/v1/flow/scenarios", params={"industry": "零售"}, headers=auth_headers
    )
    assert [item["title"] for item in by_industry.json()["items"]] == ["零售客群画像场景"]

    by_status = await client.get(
        "/api/v1/flow/scenarios", params={"status": "accepted"}, headers=auth_headers
    )
    assert by_status.json()["total"] == 0

    by_keyword = await client.get(
        "/api/v1/flow/scenarios", params={"keyword": "风控"}, headers=auth_headers
    )
    assert [item["title"] for item in by_keyword.json()["items"]] == ["金融风控联合建模"]

    paged = await client.get(
        "/api/v1/flow/scenarios", params={"page": 2, "page_size": 2}, headers=auth_headers
    )
    paged_body = paged.json()
    assert paged_body["page"] == 2 and paged_body["page_size"] == 2
    assert len(paged_body["items"]) <= 2
    assert paged_body["total"] == body["total"]


@pytest.mark.asyncio
async def test_list_rejects_unknown_filter_values(client: AsyncClient, auth_headers):
    assert (
        await client.get("/api/v1/flow/scenarios", params={"delivery_scene": "ftp"}, headers=auth_headers)
    ).status_code == 422
    assert (
        await client.get("/api/v1/flow/scenarios", params={"status": "draft"}, headers=auth_headers)
    ).status_code == 422


@pytest.mark.asyncio
async def test_detail_returns_scenario_then_bound_project(client: AsyncClient, auth_headers):
    created = await _create(client, auth_headers)

    detail = await client.get(f"/api/v1/flow/scenarios/{created['id']}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["business_background"].startswith("三家医院")
    assert detail.json()["project"] is None

    accepted = await client.post(
        f"/api/v1/flow/scenarios/{created['id']}/accept",
        json={"project_name": "肺结节联合建模", "preferred_language": "python"},
        headers=auth_headers,
    )
    assert accepted.status_code == 200, accepted.text

    after = (await client.get(f"/api/v1/flow/scenarios/{created['id']}", headers=auth_headers)).json()
    assert after["status"] == "accepted"
    assert after["project"]["id"] == accepted.json()["project"]["id"]


@pytest.mark.asyncio
async def test_detail_unknown_or_malformed_id_is_404(client: AsyncClient, auth_headers):
    assert (await client.get(f"/api/v1/flow/scenarios/{uuid.uuid4()}", headers=auth_headers)).status_code == 404
    assert (await client.get("/api/v1/flow/scenarios/not-a-uuid", headers=auth_headers)).status_code == 404
    assert (
        await client.post(f"/api/v1/flow/scenarios/{uuid.uuid4()}/accept", json={}, headers=auth_headers)
    ).status_code == 404


@pytest.mark.asyncio
async def test_accept_creates_bound_project_and_injects_scenario_config(client: AsyncClient, auth_headers):
    created = await _create(client, auth_headers)

    resp = await client.post(
        f"/api/v1/flow/scenarios/{created['id']}/accept",
        json={"project_name": "肺结节联合建模", "preferred_language": "r"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert set(payload) == {"scenario", "project"}

    scenario, project = payload["scenario"], payload["project"]
    assert scenario["id"] == created["id"]
    assert scenario["status"] == "accepted"
    assert scenario["accepted_by"] is not None

    assert project["name"] == "肺结节联合建模"
    assert project["scenario_id"] == created["id"]
    assert project["preferred_language"] == "r"
    assert project["environment_type"] == "l2c"
    assert project["status"] == "active"
    assert project["participants"][0]["role"] == "owner"

    context = project["scenario_context"]
    assert context["scenario_id"] == created["id"]
    assert context["delivery_scene"] == "tee_compute"
    assert context["compliance_constraints"] == created["compliance_constraints"]
    assert context["delivery_tech_config"] == created["delivery_tech_config"]
    assert context["suggested_resource_ids"] == created["suggested_resource_ids"]

    second = await _create(client, auth_headers, title="保险反欺诈场景")
    fallback = await client.post(
        f"/api/v1/flow/scenarios/{second['id']}/accept", json={}, headers=auth_headers
    )
    assert fallback.status_code == 200, fallback.text
    assert fallback.json()["project"]["name"] == "保险反欺诈场景"


@pytest.mark.asyncio
async def test_accept_is_idempotent_guarded(client: AsyncClient, auth_headers):
    created = await _create(client, auth_headers)
    first = await client.post(
        f"/api/v1/flow/scenarios/{created['id']}/accept", json={}, headers=auth_headers
    )
    assert first.status_code == 200

    second = await client.post(
        f"/api/v1/flow/scenarios/{created['id']}/accept", json={}, headers=auth_headers
    )
    assert second.status_code == 409
    assert "published" in second.text

    mine = (await client.get("/api/v1/flow/scenarios/mine", headers=auth_headers)).json()
    matched = [item for item in mine["items"] if item["id"] == created["id"]]
    assert len(matched) == 1 and matched[0]["project"]["id"] == first.json()["project"]["id"]


@pytest.mark.asyncio
async def test_accept_into_team_requires_membership(client: AsyncClient, admin_headers, make_user):
    provider_headers, _ = await make_user(role="data_provider", prefix="scn_prov")
    other_headers, other_id = await make_user(role="data_provider", prefix="scn_other")
    scenario = await _create(client, admin_headers)
    team_id = await _team(client, admin_headers, "承接边界团队", [other_id])

    denied = await client.post(
        f"/api/v1/flow/scenarios/{scenario['id']}/accept",
        json={"team_id": team_id},
        headers=provider_headers,
    )
    assert denied.status_code == 403

    detail = (await client.get(f"/api/v1/flow/scenarios/{scenario['id']}", headers=other_headers)).json()
    assert detail["status"] == "published"
    assert detail["project"] is None

    # 不存在 / 非法的团队 id 不能凭空调配一支团队的配额（在未被承接的场景上验）。
    fresh = await _create(client, admin_headers, title="未知团队承接场景")
    assert (
        await client.post(
            f"/api/v1/flow/scenarios/{fresh['id']}/accept",
            json={"team_id": str(uuid.uuid4())},
            headers=provider_headers,
        )
    ).status_code == 404

    allowed = await client.post(
        f"/api/v1/flow/scenarios/{scenario['id']}/accept",
        json={"team_id": team_id},
        headers=other_headers,
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["project"]["team_id"] == team_id
    assert allowed.json()["scenario"]["accepted_team_id"] == team_id


@pytest.mark.asyncio
async def test_accept_binds_lone_membership_and_names_multi_team_account(
    client: AsyncClient, admin_headers, make_user
):
    lone_headers, lone_id = await make_user(role="data_provider", prefix="scn_lone")
    solo_team = await _team(client, admin_headers, "单人团队", [lone_id])
    first = await _create(client, admin_headers, title="单人团队承接场景")
    bound = await client.post(
        f"/api/v1/flow/scenarios/{first['id']}/accept", json={}, headers=lone_headers
    )
    assert bound.status_code == 200, bound.text
    assert bound.json()["project"]["team_id"] == solo_team

    multi_team = await _team(client, admin_headers, "第二支团队", [lone_id])
    second = await _create(client, admin_headers, title="多团队承接场景")
    ambiguous = await client.post(
        f"/api/v1/flow/scenarios/{second['id']}/accept", json={}, headers=lone_headers
    )
    assert ambiguous.status_code == 422
    assert "team_id" in ambiguous.text

    assert multi_team != solo_team
    picked = await client.post(
        f"/api/v1/flow/scenarios/{second['id']}/accept",
        json={"team_id": multi_team},
        headers=lone_headers,
    )
    assert picked.status_code == 200, picked.text
    assert picked.json()["project"]["team_id"] == multi_team


@pytest.mark.asyncio
async def test_mine_lists_accepted_scenarios_per_caller(client: AsyncClient, admin_headers, make_user):
    provider_headers, _ = await make_user(role="data_provider", prefix="scn_mine")
    other_headers, _ = await make_user(role="data_provider", prefix="scn_mine_b")
    scenario = await _create(client, admin_headers)
    accepted = await client.post(
        f"/api/v1/flow/scenarios/{scenario['id']}/accept", json={}, headers=provider_headers
    )
    assert accepted.status_code == 200

    mine = await client.get("/api/v1/flow/scenarios/mine", headers=provider_headers)
    assert mine.status_code == 200
    body = mine.json()
    assert set(body) == {"items", "total", "page", "page_size"}
    hit = [item for item in body["items"] if item["id"] == scenario["id"]]
    assert len(hit) == 1
    assert hit[0]["project"]["scenario_id"] == scenario["id"]

    theirs = (await client.get("/api/v1/flow/scenarios/mine", headers=other_headers)).json()
    assert [item for item in theirs["items"] if item["id"] == scenario["id"]] == []


@pytest.mark.asyncio
async def test_writes_are_audited(client: AsyncClient, admin_headers):
    scenario = await _create(client, admin_headers)
    accepted = await client.post(
        f"/api/v1/flow/scenarios/{scenario['id']}/accept",
        json={"project_name": "审计核对项目"},
        headers=admin_headers,
    )
    assert accepted.status_code == 200

    created_rows = (
        await client.get(
            "/api/v1/audit/records",
            params={"action": "scenario.create", "keyword": scenario["id"]},
            headers=admin_headers,
        )
    ).json()
    assert created_rows["total"] >= 1
    create_hit = [row for row in created_rows["items"] if row["resource_id"] == scenario["id"]]
    assert create_hit and create_hit[0]["resource_type"] == "scenario"

    accept_rows = (
        await client.get(
            "/api/v1/audit/records",
            params={"action": "scenario.accept", "keyword": scenario["id"]},
            headers=admin_headers,
        )
    ).json()
    accept_hit = [row for row in accept_rows["items"] if row["resource_id"] == scenario["id"]]
    assert accept_hit
    assert accept_hit[0]["detail"]["project_name"] == "审计核对项目"
    assert accept_hit[0]["detail"]["injected_constraint_keys"] == ["export_approval", "output_fields"]


@pytest.mark.asyncio
async def test_scenarios_mount_before_generic_flow_runtime():
    """场景面必须先于通用 flow 面前置：先挂通用前缀会让深路径永久不可达。"""
    paths = [getattr(route, "path", "") for route in fastapi_app.routes]
    scenario_idx = next(i for i, p in enumerate(paths) if p.startswith("/api/v1/flow/scenarios"))
    runtime_idx = next(i for i, p in enumerate(paths) if p == "/api/v1/flow/runtime-status")
    assert scenario_idx < runtime_idx


@pytest.mark.asyncio
async def test_flow_runtime_surface_still_reachable(client: AsyncClient, auth_headers):
    """挂在 flow_runtime 之前不能把原有的 flow 路径顶掉。"""
    resp = await client.get("/api/v1/flow/runtime-status", headers=auth_headers)
    assert resp.status_code == 200, resp.text
