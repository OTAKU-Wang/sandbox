"""成果包预览 — GET /api/v1/deliveries/dataset/{bundle_id}/preview。

预览是**新的数据出域口子**，因此用例只钉门禁，不钉好看的数据：
1. 归属口径与下载同源（非当事方 403）。
2. 加固前生成、未经 T5 审查的包一律 409，预览不给后门。
3. 出口审查判定拦截时 403，且响应体里**一个数据字节都不许有**。
4. 包内没有表格文件时如实说"只能整包下载"，不用别的文件凑表。
"""
import io
import json
import uuid
import zipfile

import pytest

from app.models.contract import Contract
from app.models.delivery_bundle import DeliveryBundle
from app.models.user import User, UserRole
from app.services.auth_service import create_access_token, hash_password
from app.services.storage_service import storage_service

pytestmark = pytest.mark.asyncio


async def _mk_user(db, role, prefix):
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


async def _mk_party_pair(db):
    provider, provider_headers = await _mk_user(db, UserRole.DATA_PROVIDER, "pviewprov")
    buyer, buyer_headers = await _mk_user(db, UserRole.BUYER, "pviewbuyer")
    contract = Contract(
        contract_no=f"CT-{uuid.uuid4().hex[:12]}",
        contract_type="data_usage",
        status="active",
        provider_id=provider.id,
        buyer_id=buyer.id,
        title="预览测试合约",
        product_ids=[str(uuid.uuid4())],
    )
    db.add(contract)
    await db.flush()
    await db.refresh(contract)

    outsider, outsider_headers = await _mk_user(db, UserRole.BUYER, "pviewother")
    return {
        "contract": contract,
        "buyer_headers": buyer_headers,
        "provider_headers": provider_headers,
        "outsider_headers": outsider_headers,
    }


def _zip_bytes(*, reviewed: bool, members: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        manifest = {
            "bundle_id": str(uuid.uuid4()),
            "files": [{"name": name, "output_review": "passed", "output_findings": 0} for name in members],
        }
        if reviewed:
            manifest["output_review_scope"] = "all_files"
        zf.writestr("MANIFEST.json", json.dumps(manifest))
        for name, data in members.items():
            zf.writestr(f"files/{name}", data)
    return buf.getvalue()


async def _mk_bundle(db, contract):
    bundle = DeliveryBundle(
        id=uuid.uuid4(),
        contract_id=contract.id,
        session_id=None,
        created_by=contract.buyer_id,
        object_name=f"delivery/{uuid.uuid4()}.zip",
        size_bytes=1024,
        file_count=1,
    )
    db.add(bundle)
    await db.flush()
    await db.refresh(bundle)
    return bundle


def _serve(monkeypatch, payload: bytes):
    monkeypatch.setattr(storage_service, "download", lambda _name: payload)


class TestPreviewGate:
    async def test_non_party_denied_with_same_rule_as_download(self, client, db_session, monkeypatch):
        ctx = await _mk_party_pair(db_session)
        bundle = await _mk_bundle(db_session, ctx["contract"])
        _serve(monkeypatch, _zip_bytes(reviewed=True, members={"r.csv": "a,b\n1,2\n"}))

        denied = await client.get(
            f"/api/v1/deliveries/dataset/{bundle.id}/preview", headers=ctx["outsider_headers"]
        )
        assert denied.status_code == 403, denied.text

        allowed = await client.get(
            f"/api/v1/deliveries/dataset/{bundle.id}/preview", headers=ctx["buyer_headers"]
        )
        assert allowed.status_code == 200, allowed.text

    async def test_unreviewed_bundle_refused_before_any_byte(self, client, db_session, monkeypatch):
        ctx = await _mk_party_pair(db_session)
        bundle = await _mk_bundle(db_session, ctx["contract"])
        _serve(monkeypatch, _zip_bytes(reviewed=False, members={"r.csv": "a,b\n1,2\n"}))

        resp = await client.get(
            f"/api/v1/deliveries/dataset/{bundle.id}/preview", headers=ctx["buyer_headers"]
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["code"] == "OUTPUT_REVIEW_REQUIRED"
        assert "a,b" not in resp.text

    async def test_missing_bundle_is_404(self, client, db_session, monkeypatch):
        ctx = await _mk_party_pair(db_session)
        _serve(monkeypatch, _zip_bytes(reviewed=True, members={"r.csv": "a,b\n1,2\n"}))

        resp = await client.get(
            f"/api/v1/deliveries/dataset/{uuid.uuid4()}/preview", headers=ctx["buyer_headers"]
        )
        assert resp.status_code == 404, resp.text

    async def test_blocked_excerpt_returns_no_data(self, client, db_session, monkeypatch):
        ctx = await _mk_party_pair(db_session)
        bundle = await _mk_bundle(db_session, ctx["contract"])
        _serve(monkeypatch, _zip_bytes(reviewed=True, members={"r.csv": "secret,phone\nx,13800000000\n"}))
        monkeypatch.setattr("app.services.output_security.should_block", lambda _result: True)

        resp = await client.get(
            f"/api/v1/deliveries/dataset/{bundle.id}/preview", headers=ctx["buyer_headers"]
        )
        assert resp.status_code == 403, resp.text
        body = resp.json()
        assert body["code"] == "OUTPUT_BLOCKED"
        assert "13800000000" not in resp.text
        assert "data" not in json.dumps(body)


class TestPreviewShape:
    async def test_csv_excerpt_caps_rows_and_reports_total(self, client, db_session, monkeypatch):
        ctx = await _mk_party_pair(db_session)
        bundle = await _mk_bundle(db_session, ctx["contract"])
        csv = "id,val\n" + "\n".join(f"{i},v{i}" for i in range(10)) + "\n"
        _serve(monkeypatch, _zip_bytes(reviewed=True, members={"out/r.csv": csv}))

        resp = await client.get(
            f"/api/v1/deliveries/dataset/{bundle.id}/preview",
            params={"rows": 3},
            headers=ctx["buyer_headers"],
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["available"] is True
        assert body["member"] == "out/r.csv"
        assert body["rows_returned"] == 3
        assert body["row_count_total"] == 10
        assert body["truncated"] is True
        assert body["data"].strip().split("\n") == ["id,val", "0,v0", "1,v1", "2,v2"]
        assert body["output_review"]["preview_blocked"] is False

    async def test_no_tabular_member_says_so_instead_of_faking_a_table(self, client, db_session, monkeypatch):
        ctx = await _mk_party_pair(db_session)
        bundle = await _mk_bundle(db_session, ctx["contract"])
        _serve(monkeypatch, _zip_bytes(reviewed=True, members={"model.bin": "not-a-table"}))

        resp = await client.get(
            f"/api/v1/deliveries/dataset/{bundle.id}/preview", headers=ctx["buyer_headers"]
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["available"] is False
        assert "整包下载" in body["reason"]
        assert "data" not in body
        assert body["members"] == ["model.bin"]

    async def test_rows_out_of_range_rejected(self, client, db_session, monkeypatch):
        ctx = await _mk_party_pair(db_session)
        bundle = await _mk_bundle(db_session, ctx["contract"])
        _serve(monkeypatch, _zip_bytes(reviewed=True, members={"r.csv": "a,b\n1,2\n"}))

        resp = await client.get(
            f"/api/v1/deliveries/dataset/{bundle.id}/preview",
            params={"rows": 5000},
            headers=ctx["buyer_headers"],
        )
        assert resp.status_code == 422, resp.text
