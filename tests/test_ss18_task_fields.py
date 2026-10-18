"""SS-18 P0-①: 开发任务字段（task_no / 调试方法 / 任务级导出方式 / 调试与执行内存）。

判据来自 specs/ss-18-prototype-alignment.md §8 P0-①：任务编号由服务端
按当日序号生成、客户端不可指定；内存是可调参数而非档位，上限受合约已批准规格封顶
（只向下收窄）；执行内存默认继承调试内存。错误断言只看 W2 契约的 code。
"""
import re
import uuid

import pytest

from app.api import notebooks as notebooks_api
from app.models.contract import Contract
from app.models.notebook import Notebook
from app.models.user import User, UserRole
from app.services.auth_service import create_access_token, hash_password

pytestmark = pytest.mark.asyncio

TASK_NO_RE = re.compile(r"^TASK-\d{8}-\d{3}$")


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


async def _mk_contract(db, provider, buyer, memory_mb: int | None = None):
    spec = {"cpu_cores": 2, "memory_mb": memory_mb} if memory_mb else None
    contract = Contract(
        contract_no=f"CT-{uuid.uuid4().hex[:12]}",
        contract_type="data_usage",
        status="active",
        provider_id=provider.id,
        buyer_id=buyer.id,
        title="SS-18 任务字段测试合约",
        product_ids=[str(uuid.uuid4())],
        sandbox_resource_spec=spec,
    )
    db.add(contract)
    await db.flush()
    await db.refresh(contract)
    return contract


async def _parties(db):
    provider, provider_headers = await _mk_user(db, UserRole.DATA_PROVIDER, "ss18tfprov")
    buyer, buyer_headers = await _mk_user(db, UserRole.BUYER, "ss18tfbuy")
    return provider, buyer, buyer_headers


async def _create(client, headers, contract, **fields):
    return await client.post(
        "/api/v1/notebooks",
        json={"title": "任务", "path": "/nb/task.ipynb", "contract_id": str(contract.id), **fields},
        headers=headers,
    )


class TestTaskNumber:
    async def test_created_task_gets_daily_serial_and_echoes_fields(self, client, db_session):
        provider, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer, memory_mb=8192)

        first = await _create(
            client, headers, contract,
            debug_method="web_ide", export_mode="openapi", debug_memory_mb=4096,
        )
        assert first.status_code == 201, first.text
        body = first.json()
        assert TASK_NO_RE.match(body["task_no"]), body["task_no"]
        assert body["debug_method"] == "web_ide"
        assert body["export_mode"] == "openapi"
        # 执行内存未提交：继承调试内存
        assert body["debug_memory_mb"] == 4096
        assert body["exec_memory_mb"] == 4096

        second = await _create(client, headers, contract, debug_method="jupyter")
        assert second.status_code == 201, second.text
        seq_first, seq_second = int(body["task_no"][-3:]), int(second.json()["task_no"][-3:])
        assert seq_second == seq_first + 1, (body["task_no"], second.json()["task_no"])

        fetched = await client.get(f"/api/v1/notebooks/{body['id']}", headers=headers)
        assert fetched.json()["task_no"] == body["task_no"]

        listed = await client.get("/api/v1/notebooks", headers=headers)
        assert listed.status_code == 200, listed.text
        row = next(item for item in listed.json()["items"] if item["id"] == body["id"])
        # 任务列表（M2 卡片网格）读的是这条回显：少一个字段，卡面就永远显示「未记录」。
        assert (row["debug_method"], row["export_mode"]) == ("web_ide", "openapi"), row
        assert (row["debug_memory_mb"], row["exec_memory_mb"]) == (4096, 4096), row

    async def test_client_cannot_pick_task_number(self, client, db_session):
        provider, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer)
        resp = await _create(client, headers, contract, task_no="TASK-19700101-001")
        assert resp.status_code == 201, resp.text
        assert resp.json()["task_no"] != "TASK-19700101-001"

    async def test_serial_collision_is_absorbed_by_savepoint_retry(self, client, db_session, monkeypatch):
        provider, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer)
        taken = await _create(client, headers, contract)
        taken_no = taken.json()["task_no"]

        original = notebooks_api._next_task_no
        calls = {"n": 0}

        async def _first_returns_taken(db):
            calls["n"] += 1
            if calls["n"] == 1:
                return taken_no  # 模拟同瞬并发：探测到空位却已被占用
            return await original(db)

        monkeypatch.setattr(notebooks_api, "_next_task_no", _first_returns_taken)
        resp = await _create(client, headers, contract)
        assert resp.status_code == 201, resp.text
        assert resp.json()["task_no"] != taken_no
        assert calls["n"] >= 2

    async def test_legacy_rows_keep_null_task_number(self, client, db_session):
        provider, buyer, headers = await _parties(db_session)
        legacy = Notebook(
            user_id=buyer.id,
            title="存量笔记本",
            path="/nb/legacy.ipynb",
            language="python",
            content={"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5},
        )
        db_session.add(legacy)
        await db_session.flush()

        resp = await client.get("/api/v1/notebooks", headers=headers)
        assert resp.status_code == 200, resp.text
        row = next(item for item in resp.json()["items"] if item["id"] == str(legacy.id))
        assert row["task_no"] is None
        assert row["debug_memory_mb"] is None


class TestTaskMemory:
    async def test_below_two_gib_is_rejected(self, client, db_session):
        provider, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer, memory_mb=8192)
        resp = await _create(client, headers, contract, debug_memory_mb=1024)
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] == "VALIDATION_ERROR"

    async def test_above_contract_ceiling_is_refused(self, client, db_session):
        provider, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer, memory_mb=4096)
        resp = await _create(client, headers, contract, debug_memory_mb=8192)
        assert resp.status_code == 400, resp.text
        assert resp.json()["code"] == "RESOURCE_OVERRIDE_EXCEEDED"
        assert resp.json()["detail"]["contract_memory_mb"] == 4096

    async def test_exec_memory_may_exceed_debug_but_not_the_contract(self, client, db_session):
        provider, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer, memory_mb=16384)
        ok = await _create(client, headers, contract, debug_memory_mb=4096, exec_memory_mb=16384)
        assert ok.status_code == 201, ok.text
        assert ok.json()["exec_memory_mb"] == 16384

        too_big = await _create(client, headers, contract, debug_memory_mb=4096, exec_memory_mb=32768)
        assert too_big.status_code == 400, too_big.text
        assert too_big.json()["code"] == "RESOURCE_OVERRIDE_EXCEEDED"

    async def test_exec_below_debug_and_orphan_exec_are_invalid(self, client, db_session):
        provider, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer, memory_mb=32768)

        lower = await _create(client, headers, contract, debug_memory_mb=8192, exec_memory_mb=4096)
        assert lower.status_code == 422, lower.text
        assert lower.json()["code"] == "VALIDATION_ERROR"

        orphan = await _create(client, headers, contract, exec_memory_mb=8192)
        assert orphan.status_code == 422, orphan.text
        assert orphan.json()["code"] == "VALIDATION_ERROR"

    async def test_no_spec_contract_keeps_product_tiers(self, client, db_session):
        provider, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer)  # 合约未写规格
        ok = await _create(client, headers, contract, debug_memory_mb=32768, exec_memory_mb=65536)
        assert ok.status_code == 201, ok.text
        assert ok.json()["exec_memory_mb"] == 65536

        over = await _create(client, headers, contract, debug_memory_mb=65536)
        assert over.status_code == 422, over.text
        assert "32" in over.json()["message"]

    async def test_unknown_debug_method_is_rejected_by_schema(self, client, db_session):
        provider, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer)
        resp = await _create(client, headers, contract, debug_method="dbeaver")
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] == "VALIDATION_ERROR"

    async def test_unknown_contract_is_404(self, client, db_session):
        _, _, headers = await _parties(db_session)
        resp = await client.post(
            "/api/v1/notebooks",
            json={"title": "任务", "path": "/nb/x.ipynb", "contract_id": str(uuid.uuid4())},
            headers=headers,
        )
        assert resp.status_code == 404, resp.text
