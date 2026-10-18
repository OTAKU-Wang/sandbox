"""SS-19 G2: 开发任务的「已选择数据」子集注入（图 2-2 右侧已选面板）。

判据来自 specs/ss-19-full-platform-docker-e2e.md §10 与图片真值 group-B（图 2-2）：
未选 = 不限定（存量全选语义）、空选 = 明确不注入，两态必须在审计里分得开；
子集越出合约授权集必须整单拒绝（422 VALIDATION_ERROR），不能静默裁剪成授权内的部分。
"""
import uuid

import pytest
from sqlalchemy import select

from app.models.contract import Contract
from app.models.data_product import DataProduct
from app.models.data_resource import DataResource, ResourceStatus
from app.models.user import User, UserRole
from app.services import jupyter_gateway as jg
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


async def _authorized_products(db, provider, count: int):
    """建 count 组 (data_resource → data_product)，返回 (product_ids, resource_ids)。"""
    product_ids, resource_ids = [], []
    for _ in range(count):
        resource = DataResource(
            provider_id=provider.id, name=f"订单流水_{uuid.uuid4().hex[:6]}",
            status=ResourceStatus.READY.value,
        )
        db.add(resource)
        await db.flush()
        product = DataProduct(provider_id=provider.id, resource_id=resource.id, name=f"客单价_{resource.name}")
        db.add(product)
        await db.flush()
        product_ids.append(str(product.id))
        resource_ids.append(str(resource.id))
    return product_ids, resource_ids


async def _mk_contract(db, provider, buyer, product_ids):
    contract = Contract(
        contract_no=f"CT-{uuid.uuid4().hex[:12]}",
        contract_type="data_usage",
        status="active",
        provider_id=provider.id,
        buyer_id=buyer.id,
        title="SS-19 子集注入测试合约",
        product_ids=product_ids,
    )
    db.add(contract)
    await db.flush()
    await db.refresh(contract)
    return contract


class TestSubsetAtGateway:
    """注入层的三态：None=全部、[]=不注入、[x]=只注入 x。用假 db 脚本，不碰网络与 KMS。"""

    @staticmethod
    def _db(script):
        class _Res:
            def __init__(self, v):
                self._v = v

            def scalar_one_or_none(self):
                return self._v

        class _DB:
            def __init__(self):
                self._it = iter(script)

            async def execute(self, _stmt):
                return _Res(next(self._it))

        return _DB()

    @staticmethod
    def _pair(rid: str):
        from types import SimpleNamespace

        from app.models.data_resource import ResourceStatus

        product = SimpleNamespace(id=rid, name=f"产品{rid[:4]}", resource_id=rid)
        resource = SimpleNamespace(
            id=rid, name=f"资源{rid[:4]}", status=ResourceStatus.READY.value,
            storage_path=f"x/{rid}", format="csv", file_size_bytes=4,
        )
        return product, resource

    async def test_subset_injects_only_selected_and_counts_the_rest(self, tmp_path, monkeypatch):
        from types import SimpleNamespace

        from app.services.storage_service import storage_service

        rid_a, rid_b = "a" * 32, "b" * 32
        pa, ra = self._pair(rid_a)
        pb, rb = self._pair(rid_b)
        monkeypatch.setattr(storage_service, "download_decrypted", lambda _p: b"a,b\n")

        out = await jg.inject_contract_data(
            self._db([pa, ra, pb, rb]), tmp_path, SimpleNamespace(product_ids=[rid_a, rid_b]), [rid_a],
        )
        assert out["written"] == [f"{rid_a}.csv"], out
        assert out["subset"] == [rid_a]
        # 未选中的不是"失败"，是"被排除"：混进 errors 会让开发环以为供数坏了。
        assert out["errors"] == [] and out["excluded"] == 1, out
        assert not (tmp_path / "input" / f"{rid_b}.csv").exists()

    async def test_empty_subset_means_no_data_not_all_data(self, tmp_path, monkeypatch):
        from types import SimpleNamespace

        from app.services.storage_service import storage_service

        rid_a = "c" * 32
        pa, ra = self._pair(rid_a)
        monkeypatch.setattr(storage_service, "download_decrypted", lambda _p: b"a,b\n")

        out = await jg.inject_contract_data(self._db([pa, ra]), tmp_path, SimpleNamespace(product_ids=[rid_a]), [])
        assert out["written"] == [] and out["errors"] == []
        assert out["subset"] == [] and out["excluded"] == 1, out

        unset = await jg.inject_contract_data(
            self._db([pa, ra]), tmp_path / "all", SimpleNamespace(product_ids=[rid_a]), None,
        )
        assert unset["written"] == [f"{rid_a}.csv"] and unset["subset"] is None and unset["excluded"] == 0

    async def test_subset_of_unknown_resource_is_not_silently_dropped(self, tmp_path, monkeypatch):
        from types import SimpleNamespace

        from app.services.storage_service import storage_service

        rid_a = "d" * 32
        pa, ra = self._pair(rid_a)
        monkeypatch.setattr(storage_service, "download_decrypted", lambda _p: b"a,b\n")
        out = await jg.inject_contract_data(
            self._db([pa, ra]), tmp_path, SimpleNamespace(product_ids=[rid_a]), ["e" * 32],
        )
        # 请求了授权集之外的 id：注入层写不出文件，excluded=1 让审计看得见缺口。
        assert out["written"] == [] and out["excluded"] == 1 and out["subset"] == ["e" * 32], out


class TestSubsetAtApi:
    async def _parties(self, db, products=2):
        provider, _ = await _mk_user(db, UserRole.DATA_PROVIDER, "ss19g2prov")
        buyer, headers = await _mk_user(db, UserRole.BUYER, "ss19g2buy")
        product_ids, resource_ids = await _authorized_products(db, provider, products)
        contract = await _mk_contract(db, provider, buyer, product_ids)
        return provider, contract, resource_ids, headers

    async def _create(self, client, headers, contract, **fields):
        return await client.post(
            "/api/v1/notebooks",
            json={"title": "子集任务", "path": "/nb/subset.ipynb", "contract_id": str(contract.id), **fields},
            headers=headers,
        )

    async def test_authorized_subset_is_persisted_and_echoed(self, client, db_session):
        _, contract, resource_ids, headers = await self._parties(db_session)
        resp = await self._create(client, headers, contract, data_resource_ids=[resource_ids[0]])
        assert resp.status_code == 201, resp.text
        assert resp.json()["data_resource_ids"] == [resource_ids[0]], resp.text

        listed = await client.get("/api/v1/notebooks", headers=headers)
        row = next(item for item in listed.json()["items"] if item["id"] == resp.json()["id"])
        assert row["data_resource_ids"] == [resource_ids[0]], row

    async def test_unset_subset_stays_null_meaning_all_authorized(self, client, db_session):
        _, contract, _, headers = await self._parties(db_session)
        resp = await self._create(client, headers, contract)
        assert resp.status_code == 201, resp.text
        assert resp.json()["data_resource_ids"] is None

    async def test_subset_outside_contract_authorization_is_refused(self, client, db_session):
        from app.models.notebook import Notebook

        _, contract, _, headers = await self._parties(db_session)
        before = len((await db_session.execute(select(Notebook))).all())

        resp = await self._create(client, headers, contract, data_resource_ids=[str(uuid.uuid4())])
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["code"] == "VALIDATION_ERROR", body
        assert body["detail"]["errors"][0]["loc"] == ["body", "data_resource_ids"], body
        after = len((await db_session.execute(select(Notebook))).all())
        assert after == before, "越权子集必须整单拒绝，不能留下半条任务"

    async def test_subset_without_contract_is_refused(self, client, db_session):
        _, _, _, headers = await self._parties(db_session)
        resp = await client.post(
            "/api/v1/notebooks",
            json={"title": "无合约带子集", "path": "/nb/x.ipynb", "data_resource_ids": [str(uuid.uuid4())]},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] == "VALIDATION_ERROR", resp.text
