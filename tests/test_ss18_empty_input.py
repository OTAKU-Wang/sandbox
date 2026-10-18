"""SS-18 P0′: 空输入不得被判成"通过"（已落地的三处判据）。

覆盖本轮真正改掉的两类：一是空代码/空命令在校验阶段就拒，不许为一段不存在的代码起沙箱、
写执行时间、自增版本；二是空文件不再冒充"已审查通过"。真空披露（passed=True 但
no_output=True）由 /execute 与 /exec 的 security_report 承担，POSIX 导入链在 Windows 上
跑不到，这里只钉能在本机执行到的判据（见 test_exec_empty_output_discloses_no_output 的跳过条件）。
"""
import sys
import uuid

import pytest
from sqlalchemy import func, select

from app.api import deliveries as deliveries_api
from app.models.contract import Contract
from app.models.notebook import Notebook
from app.models.sandbox_session import SandboxSession
from app.models.user import User, UserRole
from app.services.auth_service import create_access_token, hash_password

_win_skip = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX-only 'resource' import chain; covered on Linux",
)

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


async def _parties(db):
    provider, provider_headers = await _mk_user(db, UserRole.DATA_PROVIDER, "ss18eiprov")
    buyer, buyer_headers = await _mk_user(db, UserRole.BUYER, "ss18eibuy")
    return provider, provider_headers, buyer, buyer_headers


async def _mk_contract(db, provider, buyer):
    contract = Contract(
        contract_no=f"CT-{uuid.uuid4().hex[:12]}",
        contract_type="data_usage",
        status="active",
        provider_id=provider.id,
        buyer_id=buyer.id,
        title="SS-18 空输入判据测试合约",
        product_ids=[str(uuid.uuid4())],
        delivery_methods=["dataset"],
    )
    db.add(contract)
    await db.flush()
    return contract


class TestEmptyCodeRejectedBeforeSandbox:
    async def _notebook_with_cell(self, client, db, headers, contract):
        created = await client.post(
            "/api/v1/notebooks",
            json={"title": "空判据", "path": "/nb/empty-guard.ipynb", "contract_id": str(contract.id)},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        body = created.json()
        doc = body["content"]
        doc["cells"].append({
            "cell_type": "code", "source": "print(1)\n", "metadata": {},
            "outputs": [], "execution_count": None,
        })
        saved = await client.put(f"/api/v1/notebooks/{body['id']}", json={"content": doc}, headers=headers)
        assert saved.status_code == 200, saved.text
        return body["id"], saved.json()["version"]

    @pytest.mark.parametrize("blank", ["", "   ", "\n\t\n  "])
    async def test_blank_cell_code_is_422_without_touching_the_sandbox(self, client, db_session, blank):
        provider, _ph, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer)
        nb_id, version = await self._notebook_with_cell(client, db_session, headers, contract)

        resp = await client.post(
            f"/api/v1/notebooks/{nb_id}/cells/execute", json={"code": blank}, headers=headers,
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"] == "code cannot be empty"

        after = await client.get(f"/api/v1/notebooks/{nb_id}", headers=headers)
        assert after.json()["version"] == version
        assert after.json()["last_executed_at"] is None
        sessions = (await db_session.execute(
            select(func.count()).select_from(SandboxSession).where(SandboxSession.user_id == buyer.id)
        )).scalar_one()
        assert sessions == 0

class TestEmptyCommandRejectedBeforeSandbox:
    @pytest.mark.parametrize("blank", ["", "  ", "\n"])
    async def test_exec_blank_command_is_422(self, client, db_session, blank):
        _provider, _ph, buyer, headers = await _parties(db_session)
        resp = await client.post(
            f"/api/v1/sandbox-sessions/{uuid.uuid4()}/exec", json={"command": blank}, headers=headers,
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"][0]["ctx"]["error"] == "command cannot be empty"

    async def test_exec_nonblank_command_still_resolves_the_session(self, client, db_session):
        _provider, _ph, buyer, headers = await _parties(db_session)
        resp = await client.post(
            f"/api/v1/sandbox-sessions/{uuid.uuid4()}/exec", json={"command": "ls"}, headers=headers,
        )
        assert resp.status_code == 404, resp.text


@_win_skip
class TestEmptyOutputDisclosure:
    async def test_exec_empty_output_discloses_no_output(self, client, db_session, monkeypatch):
        """命令成功但没有任何输出：passed=True 必须伴随 no_output=True，不能被读成"审查通过"。"""
        from app.api import sandbox_sessions as api_mod
        from app.models.data_product import DataProduct

        provider, provider_headers = await _mk_user(db_session, UserRole.DATA_PROVIDER, "ss18eiprov2")
        product = DataProduct(name="空输出判据", provider_id=provider.id, product_type="structured")
        db_session.add(product)
        await db_session.flush()
        session = SandboxSession(
            user_id=provider.id,
            data_product_id=product.id,
            sandbox_level="l2",
            sandbox_mode="structured_query",
            status="running",
            container_id="l2c-fake",
            timeout_seconds=600,
        )
        db_session.add(session)
        await db_session.flush()

        class _FakeRuntime:
            async def execute(self, container_id, code, language, **kwargs):
                return {"output": "", "exit_code": 0}

        async def _fake_manager():
            return _FakeRuntime()

        monkeypatch.setattr(api_mod, "get_sandbox_manager", _fake_manager)
        resp = await client.post(
            f"/api/v1/sandbox-sessions/{session.id}/exec", json={"command": "touch /scratch/x"},
            headers=provider_headers,
        )
        assert resp.status_code == 200, resp.text
        report = resp.json()["security_report"]
        assert report["passed"] is True
        assert report["no_output"] is True
        assert report["blocked"] is False


class TestEmptyFileIsNotReviewedClean:
    async def test_empty_text_file_is_named_empty_and_blocks_the_clean_verdict(
        self, client, db_session, monkeypatch
    ):
        provider, _ph, buyer, headers = await _parties(db_session)

        async def _fake_files(_db, _session):
            return [
                {"name": "result.csv", "data": b"user_id,amount\n1,10\n", "size_bytes": 18, "encrypted": False},
                {"name": "notes.txt", "data": b"   \n", "size_bytes": 5, "encrypted": False},
            ]

        monkeypatch.setattr(deliveries_api, "_collect_session_files", _fake_files)
        contract = await _mk_contract(db_session, provider, buyer)
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
            json={"session_id": str(session.id), "contract_id": str(contract.id), "watermark": False},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["output_review"]["empty"] == 1
        assert body["t5_review_status"] == "partially_reviewed"
