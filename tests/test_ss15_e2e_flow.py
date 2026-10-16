"""SS-15 端到端业务闭环 —— 上传数据→数据目录→合约（交付方式+自动计算频率+沙箱资源）→
在线 Notebook 开发→测试沙箱→生产发布→三条交付通道（数据集/OpenAPI/结果推送）。

与 test_ss15_flow_api.py 的分工：后者逐点验证门禁与 W2 错误体，本文件只走通
"真实用户会走的那一条路"，每一步都断言上一步的产物真的被下一步消费。
"""
import json
import uuid

import pytest

from app.models.contract import ComputeFrequency, Contract, DeliveryMethod
from app.models.user import User, UserRole
from app.services import delivery_service as dv
from app.services.auth_service import create_access_token, hash_password

pytestmark = pytest.mark.asyncio

DELIVERY_METHODS = [DeliveryMethod.DATASET, DeliveryMethod.OPEN_API, DeliveryMethod.RESULT_PUSH]


@pytest.fixture
def production_db_boundaries(db_session):
    import importlib
    from contextlib import asynccontextmanager

    from app.core.database import get_db
    from app.main import app

    nested_in = [
        "app.api.sandbox_db",
        "app.api.session_stream",
        "app.services.blockchain_adapter",
        "app.services.compute_scheduler",
        "app.services.cube_egress",
        "app.services.cube_runtime_adapter",
        "app.services.cube_template_service",
        "app.services.delivery_service",
        "app.services.kms_service",
        "app.services.session_lifecycle",
        "app.services.session_operations",
        "app.services.task_pipeline",
        "app.services.task_worker",
    ]

    @asynccontextmanager
    async def _shared():
        yield db_session

    saved = []
    for name in nested_in:
        mod = importlib.import_module(name)
        if hasattr(mod, "async_session"):
            saved.append((mod, mod.async_session))
            mod.async_session = _shared

    previous = app.dependency_overrides.get(get_db)

    async def _committing_db():
        yield db_session
        await db_session.commit()

    app.dependency_overrides[get_db] = _committing_db
    yield
    for mod, original in saved:
        mod.async_session = original
    if previous is not None:
        app.dependency_overrides[get_db] = previous


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


async def _upload_and_publish(client, provider_headers, admin_headers, name: str) -> dict:
    """阶段一：上传数据 → 生成数据目录条目（走完 submit/approve/publish 真实生命周期）。"""
    csv = "city,amount\nShanghai,100\nBeijing,200\nGuangzhou,80\n".encode("utf-8")
    upload = await client.post(
        "/api/v1/data-resources/upload",
        files={"file": (f"{uuid.uuid4().hex}.csv", csv, "text/csv")},
        data={"name": f"{name} 原始数据", "description": "SS-15 闭环测试数据"},
        headers=provider_headers,
    )
    assert upload.status_code == 200, upload.text

    created = await client.post(
        "/api/v1/data-products",
        json={
            "name": name,
            "description": f"SS-15 闭环产品 {name}",
            "product_type": "structured",
            "resource_id": upload.json()["id"],
            "industry": "finance",
            "allowed_operations": ["read", "query"],
        },
        headers=provider_headers,
    )
    assert created.status_code == 201, created.text
    product_id = created.json()["id"]

    for path, headers in ((f"/api/v1/data-products/{product_id}/submit", provider_headers),
                          (f"/api/v1/data-products/{product_id}/approve", admin_headers),
                          (f"/api/v1/data-products/{product_id}/publish", provider_headers)):
        step = await client.post(path, headers=headers)
        assert step.status_code == 200, f"{path} -> {step.text}"

    catalog = await client.get("/api/v1/data-products", headers=provider_headers)
    assert catalog.status_code == 200, catalog.text
    listed = [p for p in catalog.json()["items"] if p["id"] == product_id]
    assert listed and listed[0]["status"] == "published", "已发布产品必须出现在数据目录中"
    return created.json()


async def _configure_flow(client, buyer_headers, contract, **overrides):
    """阶段二：合约选择交付方式 + 自动计算频率 + 沙箱资源配置。"""
    payload = {
        "delivery_methods": [m.value for m in DELIVERY_METHODS],
        "auto_compute": True,
        "compute_frequency": ComputeFrequency.DAILY.value,
        "compute_minute": 30,
        "compute_hour": 2,
        "compute_tz": "Asia/Shanghai",
        "sandbox_resource_spec": {"cpu_cores": 2, "memory_mb": 2048, "disk_gb": 20},
    }
    payload.update(overrides)
    resp = await client.post(f"/api/v1/contracts/{contract.id}/flow-config", json=payload, headers=buyer_headers)
    assert resp.status_code == 200, resp.text
    return resp


async def _dev_and_publish(client, buyer_headers, contract, cell_code: str, audit_headers: dict | None = None):
    """阶段三：在线 Notebook 开发（跨单元格状态）→ 测试沙箱 → 生产发布。"""
    notebook = await client.post(
        "/api/v1/notebooks",
        json={"title": "ss15 闭环工作台", "path": "/dev/ss15-loop.ipynb", "contract_id": str(contract.id)},
        headers=buyer_headers,
    )
    assert notebook.status_code == 201, notebook.text
    nb_id = notebook.json()["id"]

    dev = await client.post(f"/api/v1/notebooks/{nb_id}/dev-session", headers=buyer_headers)
    assert dev.status_code == 200, dev.text
    assert dev.json()["env"] == "test", "开发会话必须落在测试环境"

    first = await client.post(
        f"/api/v1/notebooks/{nb_id}/cells/execute",
        json={"code": "base = 40", "cell_index": 0},
        headers=buyer_headers,
    )
    assert first.status_code == 200, first.text
    assert first.json()["ok"] is True, first.text

    second = await client.post(
        f"/api/v1/notebooks/{nb_id}/cells/execute",
        json={"code": cell_code, "cell_index": 1},
        headers=buyer_headers,
    )
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["ok"] is True, body
    assert "47" in (body.get("stdout") or "") + (body.get("repr") or ""), f"跨单元格状态必须保留: {body}"

    doc = (await client.get(f"/api/v1/notebooks/{nb_id}", headers=buyer_headers)).json()["content"]
    assert [c["source"] for c in doc["cells"]] == ["base = 40", cell_code], doc["cells"]
    assert doc["cells"][1]["outputs"], "执行输出必须回写到 notebook 文档"
    saved = await client.put(
        f"/api/v1/notebooks/{nb_id}",
        json={"content": doc, "title": "ss15 闭环工作台 v2"},
        headers=buyer_headers,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["title"] == "ss15 闭环工作台 v2", saved.text

    if audit_headers is not None:
        records = await client.get(
            "/api/v1/audit/records",
            params={"resource_type": "notebook", "limit": 100},
            headers=audit_headers,
        )
        assert records.status_code == 200, records.text
        mine = [r for r in records.json()["items"] if r["resource_id"] == nb_id]
        actions = {r["action"] for r in mine}
        assert {"notebook.created", "notebook.dev_session_created", "notebook.cell_executed", "notebook.updated"} <= actions, actions
        executed = [r for r in mine if r["action"] == "notebook.cell_executed"]
        assert len(executed) == 2, executed
        assert all(r["detail"].get("code_sha256") and "code" not in r["detail"] for r in executed), executed

    workload = await client.post(
        "/api/v1/workloads",
        json={"name": "ss15-loop", "notebook_id": nb_id, "contract_id": str(contract.id)},
        headers=buyer_headers,
    )
    assert workload.status_code == 201, workload.text
    workload_id = workload.json()["id"]
    assert workload.json()["env"] == "test"

    untested = await client.post(f"/api/v1/workloads/{workload_id}/publish", headers=buyer_headers)
    assert untested.status_code == 412, "未通过测试沙箱的工作负载禁止发布"
    assert untested.json()["code"] == "WORKLOAD_TEST_REQUIRED", untested.text
    assert untested.json()["detail"]["status"] == "draft", untested.text

    tested = await client.post(f"/api/v1/workloads/{workload_id}/test", json={}, headers=buyer_headers)
    assert tested.status_code == 200, tested.text
    assert tested.json()["passed"] is True, tested.json()

    published = await client.post(f"/api/v1/workloads/{workload_id}/publish", headers=buyer_headers)
    assert published.status_code == 200, published.text
    assert published.json()["env"] == "production"
    assert published.json()["status"] == "published"
    return nb_id, workload_id


@pytest.fixture
def local_vm_dataplane(monkeypatch):
    """把 l2c 容器内 Exec 数据面换成本机子进程：节点/卷插件 socket 属硬件依赖，
    沙箱内驱动与内核守护协议仍按真实字节跑（真进程、真 unix socket）。"""
    import asyncio
    import os
    import subprocess
    import sys
    import time

    from app.services.cube_runtime_adapter import CubeRuntimeAdapter

    async def _execute(self, container_id, code, language="python", *, session_key=None, env_vars=None, timeout=None, **_):
        env = dict(os.environ)
        env.update({str(k): str(v) for k, v in (env_vars or {}).items()})
        started = time.monotonic()
        proc = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-c", code],
            capture_output=True, text=True, env=env, timeout=timeout or 120,
        )
        return {
            "output": (proc.stdout or "") + (proc.stderr or ""),
            "exit_code": proc.returncode,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "sandbox_level": "l2c",
        }

    monkeypatch.setattr(CubeRuntimeAdapter, "execute", _execute)


class TestStageOneCatalog:

    async def test_upload_becomes_published_catalog_entry(self, client, db_session):
        provider, provider_headers = await _mk_user(db_session, UserRole.DATA_PROVIDER, "ss15e2eprov")
        _, admin_headers = await _mk_user(db_session, UserRole.ADMIN, "ss15e2eadm")
        product = await _upload_and_publish(client, provider_headers, admin_headers, "SS-15 城市消费数据")
        assert product["resource_id"], "目录条目必须绑定上传的数据资源"


class TestStageTwoContractFlow:

    async def test_flow_config_persists_all_three_dimensions(self, client, db_session):
        provider, _ = await _mk_user(db_session, UserRole.DATA_PROVIDER, "ss15e2eprov2")
        buyer, buyer_headers = await _mk_user(db_session, UserRole.BUYER, "ss15e2ebuy2")
        contract = Contract(
            contract_no=f"CT-{uuid.uuid4().hex[:12]}",
            contract_type="data_usage",
            status="active",
            provider_id=provider.id,
            buyer_id=buyer.id,
            title="SS-15 闭环合约",
            product_ids=[str(uuid.uuid4())],
        )
        db_session.add(contract)
        await db_session.flush()
        await db_session.refresh(contract)

        resp = await _configure_flow(client, buyer_headers, contract)
        body = resp.json()
        assert set(body["delivery_methods"]) == {m.value for m in DELIVERY_METHODS}
        assert body["auto_compute"] is True
        assert body["compute_frequency"] == "daily"
        assert body["sandbox_resource_spec"]["memory_mb"] == 2048
        assert body["next_compute_at"], "自动计算必须算出下一次执行时间"

        await db_session.refresh(contract)
        assert contract.compute_hour == 2 and contract.compute_minute == 30
        assert contract.sandbox_resource_spec["cpu_cores"] == 2


class TestFullLoop:

    async def test_dev_test_publish_then_openapi_delivery(self, client, db_session, l2c_api_env, production_db_boundaries, local_vm_dataplane):
        provider, provider_headers = await _mk_user(db_session, UserRole.DATA_PROVIDER, "ss15e2eprov3")
        _, admin_headers = await _mk_user(db_session, UserRole.ADMIN, "ss15e2eadm3")
        buyer, buyer_headers = await _mk_user(db_session, UserRole.BUYER, "ss15e2ebuy3")
        product = await _upload_and_publish(client, provider_headers, admin_headers, "SS-15 闭环数据")

        contract = Contract(
            contract_no=f"CT-{uuid.uuid4().hex[:12]}",
            contract_type="data_usage",
            status="active",
            provider_id=provider.id,
            buyer_id=buyer.id,
            title="SS-15 闭环合约",
            product_ids=[str(product["id"])],
        )
        db_session.add(contract)
        await db_session.flush()
        await db_session.refresh(contract)
        await _configure_flow(client, buyer_headers, contract)

        _, workload_id = await _dev_and_publish(client, buyer_headers, contract, "print(base + 7)", audit_headers=admin_headers)

        doc = await client.get(f"/api/v1/deliveries/openapi/contract/{contract.id}", headers=buyer_headers)
        assert doc.status_code == 200, doc.text
        assert doc.json()["openapi"] == "3.1.0"
        assert doc.json()["paths"] == {}, "未声明端点前文档不得凭空列出可调接口"

        endpoint = await client.post(
            "/api/v1/deliveries/openapi/endpoints",
            json={"contract_id": str(contract.id), "workload_id": str(workload_id), "name": "SS-15 结果端点", "rate_limit_per_minute": 5},
            headers=buyer_headers,
        )
        assert endpoint.status_code == 201, endpoint.text
        slug = endpoint.json()["slug"]

        doc2 = await client.get(f"/api/v1/deliveries/openapi/contract/{contract.id}", headers=buyer_headers)
        assert doc2.status_code == 200, doc2.text
        assert any(slug in p for p in doc2.json()["paths"]), doc2.json()["paths"]

        called = await client.post(f"/open/v1/{slug}", json={"limit": 10}, headers=buyer_headers)
        assert called.status_code == 200, called.text
        payload = called.json()
        assert payload["endpoint"] == slug
        assert payload["workload_hash"], "调用结果必须回指已发布工作负载的哈希"

        limited = None
        for _ in range(6):
            limited = await client.post(f"/open/v1/{slug}", json={"limit": 10}, headers=buyer_headers)
            if limited.status_code == 429:
                break
        assert limited is not None and limited.status_code == 429, "端点限流必须生效"
        assert limited.json()["code"] == "RATE_LIMITED"

    async def test_result_push_channel_queues_signed_delivery(self, client, db_session, monkeypatch):
        """结果推送通道：订阅 + 事件入队 + 签名可验，且列表不回显密钥。"""
        monkeypatch.setattr(dv.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
        from app.core.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "WEBHOOK_ALLOW_PRIVATE_URLS", True)

        provider, _ = await _mk_user(db_session, UserRole.DATA_PROVIDER, "ss15e2eprov4")
        buyer, buyer_headers = await _mk_user(db_session, UserRole.BUYER, "ss15e2ebuy4")
        contract = Contract(
            contract_no=f"CT-{uuid.uuid4().hex[:12]}",
            contract_type="data_usage",
            status="active",
            provider_id=provider.id,
            buyer_id=buyer.id,
            title="SS-15 推送合约",
            product_ids=[str(uuid.uuid4())],
        )
        db_session.add(contract)
        await db_session.flush()
        await db_session.refresh(contract)

        created = await client.post(
            "/api/v1/deliveries/webhooks",
            json={"name": "SS-15 回调", "url": "https://partner.example.com/cds-hook", "events": ["compute_run.succeeded", "workload.test_finished"], "contract_id": str(contract.id)},
            headers=buyer_headers,
        )
        assert created.status_code == 201, created.text
        secret = created.json().get("secret")
        assert secret, "创建时一次性返回密钥"
        sub_id = created.json()["id"]

        listed = await client.get("/api/v1/deliveries/webhooks", headers=buyer_headers)
        assert listed.status_code == 200, listed.text
        assert all("secret" not in item for item in listed.json()["items"])

        probed = await client.post(f"/api/v1/deliveries/webhooks/{sub_id}/test", headers=buyer_headers)
        assert probed.status_code == 200, probed.text
        deliveries = await client.get(f"/api/v1/deliveries/webhooks/{sub_id}/deliveries", headers=buyer_headers)
        assert deliveries.status_code == 200, deliveries.text
        rows = deliveries.json()["items"]
        assert rows, "测试事件必须进入投递队列"

        payload = {"contract_id": str(contract.id), "event": "compute_run.succeeded"}
        timestamp = "1760000000"
        body = json.dumps(payload, sort_keys=True)
        signature = dv.sign_payload(secret, timestamp, body)
        assert dv.verify_signature(secret, timestamp, body, signature)
        assert not dv.verify_signature(secret, timestamp, json.dumps({"other": 1}, sort_keys=True), signature)
        assert not dv.verify_signature(secret, timestamp, body, "deadbeef")

    async def test_dataset_channel_rejects_session_without_authority(self, client, db_session):
        """数据集通道必须 fail-closed：无权限会话不得打包下载。"""
        provider, _ = await _mk_user(db_session, UserRole.DATA_PROVIDER, "ss15e2eprov5")
        buyer, buyer_headers = await _mk_user(db_session, UserRole.BUYER, "ss15e2ebuy5")
        contract = Contract(
            contract_no=f"CT-{uuid.uuid4().hex[:12]}",
            contract_type="data_usage",
            status="active",
            provider_id=provider.id,
            buyer_id=buyer.id,
            title="SS-15 数据集合约",
            product_ids=[str(uuid.uuid4())],
        )
        db_session.add(contract)
        await db_session.flush()
        await db_session.refresh(contract)
        await _configure_flow(client, buyer_headers, contract)

        bundle = await client.post(
            "/api/v1/deliveries/dataset/bundle",
            json={"session_id": str(uuid.uuid4()), "contract_id": str(contract.id)},
            headers=buyer_headers,
        )
        assert bundle.status_code in (403, 404), bundle.text
        assert bundle.json()["code"] in ("SESSION_NOT_FOUND", "FORBIDDEN", "SESSION_NOT_READY"), bundle.text


class TestContractModeVocabulary:
    """合约页递到后端的 allowed_sandbox_modes 是业务词（develop/query）：
    远程部署上这曾让 dev-session 在 SandboxSessionCreate 校验里抱 500。"""

    async def test_business_vocabulary_contract_can_open_dev_session(
        self, client, db_session, l2c_api_env, production_db_boundaries, local_vm_dataplane
    ):
        provider, provider_headers = await _mk_user(db_session, UserRole.DATA_PROVIDER, "ss15e2evocab")
        _, admin_headers = await _mk_user(db_session, UserRole.ADMIN, "ss15e2eadmvocab")
        buyer, buyer_headers = await _mk_user(db_session, UserRole.BUYER, "ss15e2ebuyvocab")
        product = await _upload_and_publish(client, provider_headers, admin_headers, "SS-15 词表回归数据")

        contract = Contract(
            contract_no=f"CT-{uuid.uuid4().hex[:12]}",
            contract_type="product_dev",
            status="active",
            provider_id=provider.id,
            buyer_id=buyer.id,
            title="SS-15 词表回归合约",
            product_ids=[product["id"]],
            allowed_sandbox_modes=["develop", "query"],
        )
        db_session.add(contract)
        await db_session.flush()
        await db_session.refresh(contract)

        nb = await client.post(
            "/api/v1/notebooks",
            json={"title": "SS-15 词表回归工作台", "path": "/dev/vocab.ipynb", "contract_id": str(contract.id)},
            headers=buyer_headers,
        )
        assert nb.status_code == 201, nb.text
        nb_id = nb.json()["id"]

        denied = await client.post(f"/api/v1/notebooks/{nb_id}/dev-session", headers=provider_headers)
        assert denied.status_code in (403, 404), denied.text

        dev = await client.post(f"/api/v1/notebooks/{nb_id}/dev-session", headers=buyer_headers)
        assert dev.status_code == 200, dev.text
        assert dev.json()["sandbox_mode"] == "product_dev", "业务词 develop 必须归一到执行档 product_dev"

        cell = await client.post(
            f"/api/v1/notebooks/{nb_id}/cells/execute",
            json={"code": "print(6 * 7)", "cell_index": 0},
            headers=buyer_headers,
        )
        assert cell.status_code == 200, cell.text
        assert cell.json()["ok"] is True
