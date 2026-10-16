"""SS-15 API 面 — 交付方式门禁、发布准入 412、调度触发、webhook URL 防护、W2 错误体。"""
import io
import json
import uuid
import zipfile

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models.contract import Contract, ComputeFrequency, DeliveryMethod
from app.models.user import User, UserRole
from app.models.webhook_delivery import WebhookDelivery
from app.models.workload import Workload, WorkloadStatus
from app.models.workload_test_run import WorkloadTestRun
from app.services import delivery_service as dv
from app.services.auth_service import create_access_token, hash_password

pytestmark = pytest.mark.asyncio


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


async def _mk_contract(db, provider, buyer, **overrides):
    payload = {
        "contract_no": f"CT-{uuid.uuid4().hex[:12]}",
        "contract_type": "data_usage",
        "status": "active",
        "provider_id": provider.id,
        "buyer_id": buyer.id,
        "title": "SS-15 测试合约",
        "product_ids": [str(uuid.uuid4())],
    }
    payload.update(overrides)
    contract = Contract(**payload)
    db.add(contract)
    await db.flush()
    await db.refresh(contract)
    return contract


async def _parties(db):
    provider, provider_headers = await _mk_user(db, UserRole.DATA_PROVIDER, "ss15prov")
    buyer, buyer_headers = await _mk_user(db, UserRole.BUYER, "ss15buy")
    return provider, provider_headers, buyer, buyer_headers


class TestFlowConfig:
    async def test_buyer_sets_delivery_and_schedule(self, client, db_session):
        provider, _, buyer, buyer_headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer)

        resp = await client.post(
            f"/api/v1/contracts/{contract.id}/flow-config",
            json={
                "delivery_methods": ["dataset", "open_api", "result_push"],
                "auto_compute": True,
                "compute_frequency": "daily",
                "compute_hour": 3,
                "compute_minute": 15,
                "compute_tz": "Asia/Shanghai",
                "sandbox_resource_spec": {"cpu_cores": 2, "memory_mb": 4096},
            },
            headers=buyer_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["delivery_methods"] == ["dataset", "open_api", "result_push"]
        assert body["auto_compute"] is True
        assert body["compute_frequency"] == "daily"
        assert body["next_compute_at"] is not None
        assert body["sandbox_resource_spec"]["memory_mb"] == 4096

    async def test_manual_frequency_clears_next_run(self, client, db_session):
        provider, _, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer)

        resp = await client.post(
            f"/api/v1/contracts/{contract.id}/flow-config",
            json={"delivery_methods": ["dataset"], "auto_compute": False, "compute_frequency": "manual"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["next_compute_at"] is None

    async def test_unknown_timezone_rejected_with_w2_body(self, client, db_session):
        provider, _, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer)

        resp = await client.post(
            f"/api/v1/contracts/{contract.id}/flow-config",
            json={"delivery_methods": ["dataset"], "auto_compute": True, "compute_frequency": "daily", "compute_tz": "Mars/Olympus"},
            headers=headers,
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "INVALID_TIMEZONE"
        assert body["detail"]["compute_tz"] == "Mars/Olympus"
        assert body["request_id"]

    async def test_third_party_forbidden(self, client, db_session):
        provider, _, buyer, _ = await _parties(db_session)
        outsider, outsider_headers = await _mk_user(db_session, UserRole.BUYER, "ss15out")
        contract = await _mk_contract(db_session, provider, buyer)

        resp = await client.post(
            f"/api/v1/contracts/{contract.id}/flow-config",
            json={"delivery_methods": ["dataset"]},
            headers=outsider_headers,
        )
        assert resp.status_code == 403


class TestWorkloadPublishGate:
    async def _mk_workload(self, db, buyer, contract, entrypoint="print('hello')"):
        from app.services import workflow_service as wf

        workload = Workload(
            name="ss15-wl",
            user_id=buyer.id,
            contract_id=contract.id,
            entrypoint=entrypoint,
            language="python",
            entrypoint_hash=wf.hash_entrypoint(entrypoint),
            data_product_ids=contract.product_ids,
            env="test",
            status=WorkloadStatus.DRAFT.value,
        )
        db.add(workload)
        await db.flush()
        await db.refresh(workload)
        return workload

    async def test_publish_without_test_rejected_412(self, client, db_session):
        provider, _, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer)
        workload = await self._mk_workload(db_session, buyer, contract)

        resp = await client.post(f"/api/v1/workloads/{workload.id}/publish", headers=headers)
        assert resp.status_code == 412
        body = resp.json()
        assert body["code"] == "WORKLOAD_TEST_REQUIRED"
        assert body["detail"]["status"] == "draft"

    async def test_publish_after_entrypoint_drift_rejected(self, client, db_session):
        from app.services import workflow_service as wf

        provider, _, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer)
        workload = await self._mk_workload(db_session, buyer, contract)
        run = WorkloadTestRun(
            workload_id=workload.id,
            triggered_by=buyer.id,
            status="passed",
            passed=True,
            checks=[{"name": "entrypoint_executed", "passed": True}],
            report={"entrypoint_hash": wf.hash_entrypoint("print('other')")},
        )
        db_session.add(run)
        await db_session.flush()
        workload.test_run_id = run.id
        workload.status = WorkloadStatus.TEST_PASSED.value
        await db_session.flush()

        resp = await client.post(f"/api/v1/workloads/{workload.id}/publish", headers=headers)
        assert resp.status_code == 412
        assert resp.json()["code"] == "WORKLOAD_HASH_MISMATCH"

    async def test_publish_then_rollback_switches_version_pointer(self, client, db_session, l2c_api_env):
        from app.services import workflow_service as wf

        provider, _, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer)
        workload = await self._mk_workload(db_session, buyer, contract)
        run = WorkloadTestRun(
            workload_id=workload.id,
            triggered_by=buyer.id,
            status="passed",
            passed=True,
            checks=[{"name": "entrypoint_executed", "passed": True}],
            report={"entrypoint_hash": workload.entrypoint_hash},
        )
        db_session.add(run)
        await db_session.flush()
        workload.test_run_id = run.id
        workload.status = WorkloadStatus.TEST_PASSED.value
        await db_session.flush()

        published = await client.post(f"/api/v1/workloads/{workload.id}/publish", headers=headers)
        assert published.status_code == 200, published.text
        first = published.json()
        assert first["env"] == "production"
        assert first["status"] == "published"
        assert first["version"] == 1
        assert first["is_latest"] is True

        workload.entrypoint = "print('v2')"
        workload.entrypoint_hash = wf.hash_entrypoint(workload.entrypoint)
        run.report = {"entrypoint_hash": workload.entrypoint_hash}
        workload.status = WorkloadStatus.TEST_PASSED.value
        await db_session.flush()

        second = await client.post(f"/api/v1/workloads/{workload.id}/publish", headers=headers)
        assert second.status_code == 200, second.text
        assert second.json()["version"] == 2

        rolled = await client.post(f"/api/v1/workloads/{second.json()['id']}/rollback", headers=headers)
        assert rolled.status_code == 200, rolled.text
        assert rolled.json()["version"] == 1
        assert rolled.json()["is_latest"] is True

    async def test_workload_created_from_notebook_entrypoint(self, client, db_session):
        provider, _, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer)

        notebook = await client.post(
            "/api/v1/notebooks",
            json={"title": "ss15 nb", "path": "/dev/ss15.ipynb", "contract_id": str(contract.id)},
            headers=headers,
        )
        assert notebook.status_code == 201, notebook.text
        nb_id = notebook.json()["id"]

        doc = notebook.json()["content"]
        doc["cells"].append({"cell_type": "code", "source": "import pandas as pd\nprint(len(pd.__version__))", "metadata": {}, "outputs": [], "execution_count": None})
        saved = await client.put(f"/api/v1/notebooks/{nb_id}", json={"content": doc}, headers=headers)
        assert saved.status_code == 200, saved.text
        assert saved.json()["version"] >= 2

        created = await client.post(
            "/api/v1/workloads",
            json={"name": "from-nb", "notebook_id": nb_id, "contract_id": str(contract.id)},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        from app.services import workflow_service as wf

        expected_hash = wf.hash_entrypoint("import pandas as pd\nprint(len(pd.__version__))")
        assert created.json()["entrypoint_hash"] == expected_hash
        assert created.json()["env"] == "test"
        assert created.json()["status"] == "draft"

    async def test_workload_without_entrypoint_rejected(self, client, db_session):
        provider, _, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer)

        resp = await client.post("/api/v1/workloads", json={"name": "empty", "contract_id": str(contract.id), "entrypoint": "   "}, headers=headers)
        assert resp.status_code == 400


class TestNotebookEmptyRun:
    async def test_run_without_cells_is_a_conflict_not_a_vacuous_pass(self, client, db_session):
        provider, _, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer)

        notebook = await client.post(
            "/api/v1/notebooks",
            json={"title": "empty nb", "path": "/dev/empty.ipynb", "contract_id": str(contract.id)},
            headers=headers,
        )
        assert notebook.status_code == 201, notebook.text
        nb_id = notebook.json()["id"]
        assert notebook.json()["content"]["cells"] == []

        run = await client.post(f"/api/v1/notebooks/{nb_id}/run", headers=headers)
        assert run.status_code == 409, run.text
        assert run.json()["code"] == "NOTEBOOK_NO_CELLS"

        after = await client.get(f"/api/v1/notebooks/{nb_id}", headers=headers)
        assert after.json()["version"] == notebook.json()["version"]
        assert after.json()["execution_backend"] is None

    async def test_run_with_blank_cells_is_also_refused(self, client, db_session):
        provider, _, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer)

        notebook = await client.post(
            "/api/v1/notebooks",
            json={"title": "blank nb", "path": "/dev/blank.ipynb", "contract_id": str(contract.id)},
            headers=headers,
        )
        nb_id = notebook.json()["id"]
        doc = notebook.json()["content"]
        doc["cells"].append({"cell_type": "code", "source": "   \n", "metadata": {}, "outputs": [], "execution_count": None})
        saved = await client.put(f"/api/v1/notebooks/{nb_id}", json={"content": doc}, headers=headers)
        assert saved.status_code == 200, saved.text

        run = await client.post(f"/api/v1/notebooks/{nb_id}/run", headers=headers)
        assert run.status_code == 409, run.text
        assert run.json()["code"] == "NOTEBOOK_NO_CELLS"


class TestNotebookContentTransport:
    """笔记内容的载体契约：库里是 nbformat 4 文档，但编辑器递上来的是它的 JSON 字符串。"""

    async def _mk_notebook(self, client, headers, contract):
        notebook = await client.post(
            "/api/v1/notebooks",
            json={"title": "ss15 transport", "path": "/dev/transport.ipynb", "contract_id": str(contract.id)},
            headers=headers,
        )
        assert notebook.status_code == 201, notebook.text
        return notebook.json()["id"], notebook.json()["content"]

    async def test_content_accepts_json_string_from_editor(self, client, db_session):
        provider, _, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer)
        nb_id, doc = await self._mk_notebook(client, headers, contract)
        doc["cells"] = [{"cell_type": "code", "source": "print(40 + 2)", "metadata": {}, "outputs": [], "execution_count": None}]

        saved = await client.put(f"/api/v1/notebooks/{nb_id}", json={"content": json.dumps(doc)}, headers=headers)
        assert saved.status_code == 200, saved.text
        assert saved.json()["version"] >= 2

        fetched = await client.get(f"/api/v1/notebooks/{nb_id}", headers=headers)
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["content"]["cells"][0]["source"] == "print(40 + 2)"

        rebuilt = await client.post(
            "/api/v1/workloads",
            json={"name": "from-editor-string", "notebook_id": nb_id, "contract_id": str(contract.id)},
            headers=headers,
        )
        assert rebuilt.status_code == 201, rebuilt.text
        from app.services import workflow_service as wf

        assert rebuilt.json()["entrypoint_hash"] == wf.hash_entrypoint("print(40 + 2)")

    async def test_content_document_still_accepted(self, client, db_session):
        provider, _, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer)
        nb_id, doc = await self._mk_notebook(client, headers, contract)
        doc["cells"] = [{"cell_type": "code", "source": "x = 1", "metadata": {}, "outputs": [], "execution_count": None}]

        saved = await client.put(f"/api/v1/notebooks/{nb_id}", json={"content": doc}, headers=headers)
        assert saved.status_code == 200, saved.text
        assert saved.json()["version"] >= 2

    async def test_non_document_content_rejected_400_not_500(self, client, db_session):
        provider, _, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer)
        nb_id, _ = await self._mk_notebook(client, headers, contract)

        plain = await client.put(f"/api/v1/notebooks/{nb_id}", json={"content": "print(1)"}, headers=headers)
        assert plain.status_code == 400, plain.text
        assert "notebook content" in plain.text

        array = await client.put(f"/api/v1/notebooks/{nb_id}", json={"content": '[{"cell_type": "code"}]'}, headers=headers)
        assert array.status_code == 400, array.text
        assert array.json()["code"] == "HTTP_400"


class TestComputeApi:
    async def test_manual_run_without_published_workload_fails_honestly(self, client, db_session):
        provider, _, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer)

        resp = await client.post("/api/v1/compute/runs", json={"contract_id": str(contract.id)}, headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "failed"
        assert "生产工作负载" in body["error_message"]
        assert body["trigger"] == "manual"

    async def test_third_party_cannot_trigger(self, client, db_session):
        provider, _, buyer, _ = await _parties(db_session)
        outsider, outsider_headers = await _mk_user(db_session, UserRole.BUYER, "ss15out2")
        contract = await _mk_contract(db_session, provider, buyer)

        resp = await client.post("/api/v1/compute/runs", json={"contract_id": str(contract.id)}, headers=outsider_headers)
        assert resp.status_code == 403

    async def test_schedule_lists_contract(self, client, db_session):
        provider, _, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer, auto_compute=True, compute_frequency=ComputeFrequency.HOURLY.value, compute_minute=7)

        resp = await client.get("/api/v1/compute/schedule", headers=headers)
        assert resp.status_code == 200
        row = next(i for i in resp.json()["items"] if i["contract_id"] == str(contract.id))
        assert row["compute_frequency"] == "hourly"
        assert row["compute_minute"] == 7


class TestDeliveryGates:
    async def test_dataset_bundle_requires_configured_method(self, client, db_session):
        provider, _, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer, delivery_methods=[])

        resp = await client.post(
            "/api/v1/deliveries/dataset/bundle",
            json={"session_id": str(uuid.uuid4()), "contract_id": str(contract.id)},
            headers=headers,
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == "DELIVERY_METHOD_NOT_ALLOWED"

    async def test_openapi_endpoint_requires_published_workload(self, client, db_session):
        provider, _, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer, delivery_methods=[DeliveryMethod.OPEN_API.value])

        resp = await client.post(
            "/api/v1/deliveries/openapi/endpoints",
            json={"contract_id": str(contract.id), "name": "report"},
            headers=headers,
        )
        assert resp.status_code == 412
        assert resp.json()["code"] == "WORKLOAD_NOT_PUBLISHED"

    async def test_openapi_document_lists_nothing_before_publish(self, client, db_session):
        provider, _, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer, delivery_methods=[DeliveryMethod.OPEN_API.value])

        resp = await client.get(f"/api/v1/deliveries/openapi/contract/{contract.id}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["paths"] == {}
        assert resp.json()["openapi"] == "3.1.0"


class TestWebhookApi:
    async def test_private_url_rejected_when_gate_closed(self, client, db_session, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "WEBHOOK_ALLOW_PRIVATE_URLS", False)
        provider, _, buyer, headers = await _parties(db_session)

        resp = await client.post(
            "/api/v1/deliveries/webhooks",
            json={"name": "hook", "url": "http://127.0.0.1:9/hook", "events": ["compute_run.succeeded"]},
            headers=headers,
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "WEBHOOK_URL_REJECTED"

    async def test_unsupported_event_rejected(self, client, db_session):
        provider, _, buyer, headers = await _parties(db_session)

        resp = await client.post(
            "/api/v1/deliveries/webhooks",
            json={"name": "hook", "url": "https://partner.example.com/hook", "events": ["not.a.real.event"]},
            headers=headers,
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "WEBHOOK_EVENT_UNSUPPORTED"

    async def test_secret_returned_once_and_list_omits_it(self, client, db_session, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "WEBHOOK_ALLOW_PRIVATE_URLS", True)
        monkeypatch.setattr(dv.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
        provider, _, buyer, headers = await _parties(db_session)

        created = await client.post(
            "/api/v1/deliveries/webhooks",
            json={"name": "hook", "url": "https://partner.example.com/hook", "events": ["compute_run.succeeded"]},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        secret = created.json()["secret"]
        assert len(secret) >= 16

        listed = await client.get("/api/v1/deliveries/webhooks", headers=headers)
        assert listed.status_code == 200
        row = next(i for i in listed.json()["items"] if i["id"] == created.json()["id"])
        assert "secret" not in row

        rotated = await client.post(f"/api/v1/deliveries/webhooks/{created.json()['id']}/rotate-secret", headers=headers)
        assert rotated.status_code == 200
        assert rotated.json()["secret"] != secret

    async def test_delete_keeps_delivery_history(self, client, db_session, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "WEBHOOK_ALLOW_PRIVATE_URLS", True)
        monkeypatch.setattr(dv.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
        provider, _, buyer, headers = await _parties(db_session)

        created = await client.post(
            "/api/v1/deliveries/webhooks",
            json={"name": "hook", "url": "https://partner.example.com/hook", "events": ["compute_run.succeeded"]},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        sub_id = uuid.UUID(created.json()["id"])

        delivery = WebhookDelivery(
            subscription_id=sub_id,
            event_type="webhook.test",
            event_id=uuid.uuid4().hex,
            payload={"message": "history"},
            status="failed",
        )
        db_session.add(delivery)
        await db_session.flush()
        delivery_id = delivery.id

        deleted = await client.delete(f"/api/v1/deliveries/webhooks/{sub_id}", headers=headers)
        assert deleted.status_code == 204, deleted.text

        row = (await db_session.execute(select(WebhookDelivery).where(WebhookDelivery.id == delivery_id))).scalar_one_or_none()
        assert row is not None, "投递历史不得随订阅删除而清空"
        assert row.subscription_id is None, "订阅删除后历史应置空外键，而不是卡住删除"

        gone = await client.get("/api/v1/deliveries/webhooks", headers=headers)
        assert gone.status_code == 200
        assert sub_id.hex not in [i["id"].replace("-", "") for i in gone.json()["items"]]


class TestNotebookAuditTrail:
    async def test_create_and_archive_are_audited(self, client, db_session):
        provider, _, buyer, buyer_headers = await _parties(db_session)
        _, admin_headers = await _mk_user(db_session, UserRole.ADMIN, "ss15adm")
        contract = await _mk_contract(db_session, provider, buyer)

        created = await client.post(
            "/api/v1/notebooks",
            json={"title": "审计用例", "path": "/dev/audit.ipynb", "contract_id": str(contract.id)},
            headers=buyer_headers,
        )
        assert created.status_code == 201, created.text
        nb_id = created.json()["id"]

        archived = await client.delete(f"/api/v1/notebooks/{nb_id}", headers=buyer_headers)
        assert archived.status_code == 204, archived.text

        records = await client.get(
            "/api/v1/audit/records",
            params={"resource_type": "notebook", "limit": 100},
            headers=admin_headers,
        )
        assert records.status_code == 200, records.text
        mine = [r for r in records.json()["items"] if r["resource_id"] == nb_id]
        assert {r["action"] for r in mine} == {"notebook.created", "notebook.archived"}, mine


class TestDatasetBundleOwnership:
    """成果包下载必须按合约当事方鉴权：bundle_id 不可猜，但不可猜不等于已授权。"""

    async def _make_bundle(self, client, db_session, monkeypatch, buyer, headers):
        from app.api import deliveries as deliveries_api
        from app.models.sandbox_session import SandboxSession

        async def _fake_files(db, session):
            return [
                {"name": "result.txt", "data": "分组均值 3.14\n".encode("utf-8"), "size_bytes": 24, "encrypted": False},
                {"name": "weights.bin", "data": b"\x00\x01\x02\x03", "size_bytes": 4, "encrypted": False},
            ]

        monkeypatch.setattr(deliveries_api, "_collect_session_files", _fake_files)
        provider, _ = await _mk_user(db_session, UserRole.DATA_PROVIDER, "ss15ownprov")
        contract = await _mk_contract(db_session, provider, buyer, delivery_methods=["dataset"])
        session = SandboxSession(
            user_id=buyer.id,
            data_product_id=uuid.uuid4(),
            contract_id=contract.id,
            sandbox_level="l3",
            sandbox_mode="structured_query",
            status="completed",
            timeout_seconds=3600,
        )
        db_session.add(session)
        await db_session.flush()
        resp = await client.post(
            "/api/v1/deliveries/dataset/bundle",
            json={"session_id": str(session.id), "contract_id": str(contract.id), "watermark": True},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    async def test_party_downloads_watermarked_zip(self, client, db_session, monkeypatch):
        _, _, buyer, headers = await _parties(db_session)
        body = await self._make_bundle(client, db_session, monkeypatch, buyer, headers)

        dl = await client.get(body["download_url"], headers=headers)
        assert dl.status_code == 200, dl.text
        assert dl.headers["content-disposition"].startswith("attachment;")
        archive = zipfile.ZipFile(io.BytesIO(dl.content))
        assert {"files/result.txt", "files/weights.bin", "MANIFEST.json"} <= set(archive.namelist())
        manifest = json.loads(archive.read("MANIFEST.json"))
        flags = {item["name"]: item["watermark_applied"] for item in manifest["files"]}
        assert flags == {"result.txt": True, "weights.bin": False}

    async def test_non_party_cannot_download(self, client, db_session, monkeypatch):
        _, _, buyer, headers = await _parties(db_session)
        body = await self._make_bundle(client, db_session, monkeypatch, buyer, headers)
        _stranger, stranger_headers = await _mk_user(db_session, UserRole.BUYER, "ss15stranger")

        dl = await client.get(body["download_url"], headers=stranger_headers)
        assert dl.status_code in (403, 404), dl.text


class TestFlowRuntimeStatus:
    """SS-13 fail-closed 的产品面：无 MicroVM 节点时，不可用要在点击之前就可读。"""

    async def test_disabled_dataplane_reports_honest_reason(self, client, db_session, monkeypatch):
        monkeypatch.setattr(get_settings(), "CUBE_DATAPLANE_ENABLED", False)
        _buyer, headers = await _mk_user(db_session, UserRole.BUYER, "ss15rtoff")

        resp = await client.get("/api/v1/flow/runtime-status", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["available"] is False
        assert body["reason"] == "dataplane_disabled"
        assert body["message"] and "MicroVM" in body["message"]
        assert body["checked_at"]

    async def test_enabled_without_online_node_reports_no_ready_node(self, client, db_session, monkeypatch):
        from app.models.sandbox_node import NodeStatus, SandboxNode

        monkeypatch.setattr(get_settings(), "CUBE_DATAPLANE_ENABLED", True)
        _buyer, headers = await _mk_user(db_session, UserRole.BUYER, "ss15rtnode")
        for node in (await db_session.execute(select(SandboxNode))).scalars().all():
            node.status = NodeStatus.OFFLINE.value
        await db_session.flush()

        body = (await client.get("/api/v1/flow/runtime-status", headers=headers)).json()
        assert body["available"] is False
        assert body["reason"] == "no_ready_node"
        assert body["ready_nodes"] == 0
        assert body["message"]

    async def test_enabled_with_online_node_is_available(self, client, db_session, monkeypatch):
        from app.models.sandbox_node import NodeStatus, SandboxNode

        monkeypatch.setattr(get_settings(), "CUBE_DATAPLANE_ENABLED", True)
        _buyer, headers = await _mk_user(db_session, UserRole.BUYER, "ss15rton")
        node = (await db_session.execute(select(SandboxNode))).scalars().first()
        assert node is not None, "conftest 的 ss13 cube 节点未植入"
        node.status = NodeStatus.ONLINE.value
        await db_session.flush()

        body = (await client.get("/api/v1/flow/runtime-status", headers=headers)).json()
        assert body["available"] is True
        assert body["reason"] is None
        assert body["message"] is None
        assert body["ready_nodes"] >= 1

    async def test_requires_authentication(self, client):
        resp = await client.get("/api/v1/flow/runtime-status")
        assert resp.status_code in (401, 403), resp.text


class TestDatasetBundleOutputReview:
    """P4·安：打包交付与单文件下载（SS-14 RT-07）必须同走 T5 出口审查，
    否则“单下拦截、打包带走”就是围堆。拦截必须在上传之前，不得留下部分交付。"""

    async def _bundle(self, client, db, monkeypatch, files):
        from app.api import deliveries as deliveries_api
        from app.models.sandbox_session import SandboxSession
        from app.services.storage_service import storage_service

        async def _fake_files(db_, session_):
            return files

        uploaded: list = []
        original_upload = storage_service.upload

        def _spy_upload(*a, **k):
            uploaded.append(a)
            return original_upload(*a, **k)

        monkeypatch.setattr(deliveries_api, "_collect_session_files", _fake_files)
        monkeypatch.setattr(storage_service, "upload", _spy_upload)

        provider, _, buyer, headers = await _parties(db)
        contract = await _mk_contract(db, provider, buyer, delivery_methods=["dataset"])
        session = SandboxSession(
            user_id=buyer.id,
            data_product_id=uuid.uuid4(),
            contract_id=contract.id,
            sandbox_level="l3",
            sandbox_mode="structured_query",
            status="completed",
            timeout_seconds=3600,
        )
        db.add(session)
        await db.flush()
        resp = await client.post(
            "/api/v1/deliveries/dataset/bundle",
            json={"session_id": str(session.id), "contract_id": str(contract.id), "watermark": False},
            headers=headers,
        )
        return resp, headers, uploaded

    async def test_critical_pii_blocks_bundle_and_uploads_nothing(self, client, db_session, monkeypatch):
        from app.models.delivery_bundle import DeliveryBundle

        files = [{"name": "leak.txt", "data": "客户 110101199003074578 的记录\n".encode("utf-8"), "size_bytes": 40, "encrypted": False}]
        resp, _headers, uploaded = await self._bundle(client, db_session, monkeypatch, files)

        assert resp.status_code == 502, resp.text
        body = resp.json()
        assert body["code"] == "OUTPUT_BLOCKED"
        assert body["detail"]["file"] == "leak.txt"
        assert uploaded == [], "拦截后不得向对象存储写入包体"
        rows = (await db_session.execute(select(DeliveryBundle))).scalars().all()
        assert rows == []

        from app.models.audit_log import AuditLog

        audits = (await db_session.execute(
            select(AuditLog).where(AuditLog.action == "delivery.dataset.blocked")
        )).scalars().all()
        assert len(audits) == 1, "拦截必须写审计"
        assert audits[0].detail["file"] == "leak.txt"

    async def test_high_severity_text_ships_redacted(self, client, db_session, monkeypatch):
        files = [{"name": "contact.txt", "data": "联系手机 13800138000\n".encode("utf-8"), "size_bytes": 28, "encrypted": False}]
        resp, headers, uploaded = await self._bundle(client, db_session, monkeypatch, files)

        assert resp.status_code == 201, resp.text
        assert len(uploaded) == 1
        body = resp.json()
        assert body["output_review"]["findings_total"] >= 1

        dl = await client.get(body["download_url"], headers=headers)
        assert dl.status_code == 200, dl.text
        archive = zipfile.ZipFile(io.BytesIO(dl.content))
        shipped = archive.read("files/contact.txt").decode("utf-8")
        assert "[REDACTED:phone]" in shipped, shipped
        assert "13800138000" not in shipped
        manifest = json.loads(archive.read("MANIFEST.json"))
        assert {i["name"]: i["output_review"] for i in manifest["files"]} == {"contact.txt": "redacted"}

    async def test_binary_files_are_disclosed_as_unreviewed(self, client, db_session, monkeypatch):
        files = [
            {"name": "summary.txt", "data": "分组均值 3.14，样本数 128\n".encode("utf-8"), "size_bytes": 30, "encrypted": False},
            {"name": "weights.bin", "data": b"\x00\x01\xff\xfe", "size_bytes": 4, "encrypted": False},
        ]
        resp, headers, _uploaded = await self._bundle(client, db_session, monkeypatch, files)

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["output_review"]["skipped_binary"] == 1
        dl = await client.get(body["download_url"], headers=headers)
        archive = zipfile.ZipFile(io.BytesIO(dl.content))
        assert archive.read("files/weights.bin") == b"\x00\x01\xff\xfe"
        manifest = json.loads(archive.read("MANIFEST.json"))
        assert {i["name"]: i["output_review"] for i in manifest["files"]} == {
            "summary.txt": "passed",
            "weights.bin": "skipped_binary",
        }
        assert "skipped_binary" in manifest["output_review_scope"]

    async def test_bundle_without_review_marker_is_refused(self, client, db_session, monkeypatch):
        """加固前生成的存量包字节未经 T5：按 409 拒绝并要求重新生成。"""
        from app.models.delivery_bundle import DeliveryBundle
        from app.models.sandbox_session import SandboxSession
        from app.services.storage_service import storage_service

        provider, _, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer, delivery_methods=["dataset"])
        session = SandboxSession(
            user_id=buyer.id,
            data_product_id=uuid.uuid4(),
            contract_id=contract.id,
            sandbox_level="l3",
            sandbox_mode="structured_query",
            status="completed",
            timeout_seconds=3600,
        )
        db_session.add(session)
        await db_session.flush()

        bundle_id = uuid.uuid4()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("files/legacy.txt", "历史产出\n")
            zf.writestr("MANIFEST.json", json.dumps({"bundle_id": str(bundle_id), "files": []}))
        payload = buffer.getvalue()
        object_name = f"delivery/{bundle_id}.zip"
        storage_service.upload(payload, object_name, content_type="application/zip")
        db_session.add(
            DeliveryBundle(
                id=bundle_id,
                contract_id=contract.id,
                session_id=session.id,
                created_by=buyer.id,
                object_name=object_name,
                size_bytes=len(payload),
                file_count=1,
            )
        )
        await db_session.flush()

        resp = await client.get(f"/api/v1/deliveries/dataset/{bundle_id}/download", headers=headers)
        assert resp.status_code == 409, resp.text
        assert resp.json()["code"] == "OUTPUT_REVIEW_REQUIRED"

        from app.models.audit_log import AuditLog

        audits = (await db_session.execute(
            select(AuditLog).where(AuditLog.action == "delivery.dataset.review_required")
        )).scalars().all()
        assert len(audits) == 1

