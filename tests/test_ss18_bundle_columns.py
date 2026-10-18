"""SS-18 P0-②: 交付中心展示列（T5 审查状态 / 内容水印状态 / 数据表行数）落库与回显。

判据：三项结论在打包当时写进 delivery_bundles 行，列表端点只读行、不下载包体反推；
这两列之前的包保持 NULL（显示「未记录」），不能被补成已审/已加水印。行数只数数据表
文件（.csv/.tsv/.jsonl），纯文本与二进制不计入——未知不等于 0 行。
"""
import uuid

import pytest

from app.api import deliveries as deliveries_api
from app.models.contract import Contract
from app.models.delivery_bundle import DeliveryBundle
from app.models.sandbox_session import SandboxSession
from app.models.user import User, UserRole
from app.services.auth_service import create_access_token, hash_password

BUNDLES_URL = "/api/v1/deliveries/dataset/bundles"

CSV_BODY = "user_id,amount\n1,10\n2,20\n3,30\n"
JSONL_BODY = '{"score": 0.1}\n{"score": 0.2}\n{"score": 0.3}\n{"score": 0.4}\n'


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
    provider, provider_headers = await _mk_user(db, UserRole.DATA_PROVIDER, "ss18bcprov")
    buyer, buyer_headers = await _mk_user(db, UserRole.BUYER, "ss18bcbuy")
    return provider, provider_headers, buyer, buyer_headers


async def _mk_contract(db, provider, buyer):
    contract = Contract(
        contract_no=f"CT-{uuid.uuid4().hex[:12]}",
        contract_type="data_usage",
        status="active",
        provider_id=provider.id,
        buyer_id=buyer.id,
        title="SS-18 展示列测试合约",
        product_ids=[str(uuid.uuid4())],
        delivery_methods=["dataset"],
    )
    db.add(contract)
    await db.flush()
    await db.refresh(contract)
    return contract


class TestRowCountHelper:
    def test_only_data_tables_are_counted(self):
        assert deliveries_api._data_row_count("r.csv", CSV_BODY.encode()) == 3
        assert deliveries_api._data_row_count("r.jsonl", JSONL_BODY.encode()) == 4
        assert deliveries_api._data_row_count("readme.txt", b"1\n2\n3\n") is None
        assert deliveries_api._data_row_count("w.bin", b"\x00\x01") is None

    def test_header_only_and_undecodable(self):
        assert deliveries_api._data_row_count("r.csv", b"user_id,amount\n") == 0
        assert deliveries_api._data_row_count("r.csv", b"\xff\xfe\x00bad") is None

    def test_blank_lines_are_not_rows(self):
        assert deliveries_api._data_row_count("r.csv", b"a,b\n\n1,2\n\n") == 1


class TestAggregateStatuses:
    def test_findings_outrank_unreviewed_binary(self):
        files = [{"output_review": "skipped_binary"}, {"output_review": "redacted"}]
        assert deliveries_api._t5_review_status(files) == "passed_with_findings"

    def test_binary_bearing_bundle_is_only_partially_reviewed(self):
        files = [{"output_review": "passed"}, {"output_review": "skipped_binary"}]
        assert deliveries_api._t5_review_status(files) == "partially_reviewed"

    def test_all_clean_is_passed(self):
        assert deliveries_api._t5_review_status([{"output_review": "passed"}]) == "passed"

    def test_watermark_never_claims_applied_over_binaries(self):
        mixed = [{"watermark_applied": True}, {"watermark_applied": False}]
        assert deliveries_api._watermark_status(True, mixed) == "partially_applied"
        assert deliveries_api._watermark_status(True, [{"watermark_applied": False}]) == "not_applied"
        assert deliveries_api._watermark_status(True, [{"watermark_applied": True}]) == "applied"
        assert deliveries_api._watermark_status(False, [{"watermark_applied": False}]) == "not_requested"


class TestBundleRowPersistence:
    pytestmark = pytest.mark.asyncio

    async def _make(self, client, db, monkeypatch, buyer, headers, files, *, watermark=True):
        """采集面注入假文件，打包/落库/列表全部走真端点：结论必须由端点算出。"""

        async def _fake_files(_db, _session):
            return [
                {
                    "name": name,
                    "data": body.encode("utf-8") if isinstance(body, str) else body,
                    "size_bytes": len(body),
                    "encrypted": False,
                }
                for name, body in files
            ]

        monkeypatch.setattr(deliveries_api, "_collect_session_files", _fake_files)
        provider, _ = await _mk_user(db, UserRole.DATA_PROVIDER, "ss18bcmakeprov")
        contract = await _mk_contract(db, provider, buyer)
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
            json={"session_id": str(session.id), "contract_id": str(contract.id), "watermark": watermark},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        return contract, session, resp.json()

    async def test_mixed_bundle_reports_partial_review_and_rows(self, client, db_session, monkeypatch):
        provider, _ph, buyer, headers = await _parties(db_session)
        contract, _session, body = await self._make(
            client, db_session, monkeypatch, buyer, headers,
            [("result.csv", CSV_BODY), ("notes.txt", "结论\n"), ("weights.bin", b"\xff\xfe\x01\x02")],
        )
        assert body["row_count"] == 3
        assert body["t5_review_status"] == "partially_reviewed"
        assert body["watermark_status"] == "partially_applied"

        row = await db_session.get(DeliveryBundle, uuid.UUID(body["bundle_id"]))
        assert (row.t5_review_status, row.watermark_status, row.row_count) == (
            "partially_reviewed", "partially_applied", 3,
        )

        listed = await client.get(BUNDLES_URL, headers=headers)
        assert listed.status_code == 200, listed.text
        item = next(i for i in listed.json()["items"] if i["bundle_id"] == body["bundle_id"])
        assert item["t5_review_status"] == "partially_reviewed"
        assert item["watermark_status"] == "partially_applied"
        assert item["row_count"] == 3
        assert item["contract_id"] == str(contract.id)

    async def test_text_only_bundle_is_fully_watermarked_and_reviewed(self, client, db_session, monkeypatch):
        _provider, _ph, buyer, headers = await _parties(db_session)
        _contract, _session, body = await self._make(
            client, db_session, monkeypatch, buyer, headers,
            [("scores.jsonl", JSONL_BODY), ("notes.txt", "结论\n")],
        )
        assert body["t5_review_status"] == "passed"
        assert body["watermark_status"] == "applied"
        assert body["row_count"] == 4

    async def test_unrequested_watermark_is_not_reported_as_failure(self, client, db_session, monkeypatch):
        _provider, _ph, buyer, headers = await _parties(db_session)
        _contract, _session, body = await self._make(
            client, db_session, monkeypatch, buyer, headers,
            [("result.csv", CSV_BODY)], watermark=False,
        )
        assert body["watermark_status"] == "not_requested"
        assert body["row_count"] == 3

    async def test_bundle_without_data_tables_has_unknown_rows_not_zero(self, client, db_session, monkeypatch):
        _provider, _ph, buyer, headers = await _parties(db_session)
        _contract, _session, body = await self._make(
            client, db_session, monkeypatch, buyer, headers,
            [("notes.txt", "只有说明\n"), ("weights.bin", b"\xff\xfe\x01\x02")],
        )
        assert body["row_count"] is None

    async def test_legacy_row_stays_unrecorded(self, client, db_session):
        provider, _ph, buyer, headers = await _parties(db_session)
        contract = await _mk_contract(db_session, provider, buyer)
        legacy = DeliveryBundle(
            id=uuid.uuid4(),
            contract_id=contract.id,
            session_id=None,
            created_by=buyer.id,
            object_name=f"delivery/{uuid.uuid4()}.zip",
            size_bytes=1024,
            file_count=2,
        )
        db_session.add(legacy)
        await db_session.flush()

        listed = await client.get(BUNDLES_URL, headers=headers)
        assert listed.status_code == 200, listed.text
        item = next(i for i in listed.json()["items"] if i["bundle_id"] == str(legacy.id))
        assert item["t5_review_status"] is None
        assert item["watermark_status"] is None
        assert item["row_count"] is None
