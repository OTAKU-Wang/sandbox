"""SS-18 P0-③: OpenAPI 调用明细（来源 IP 落库 + 端点级只读查询）。

判据：数据面每次调用（成功与被拒的不可用）都要带上来源 IP 落到带锚定的审计日志上，
并**按被调端点归源**（resource_type=api_endpoint），使 GET
/deliveries/openapi/endpoints/{id}/calls 能走 ix_audit_logs_resource 精确分页；
改口径之前的历史行按合约归源，读侧不认领、也不反填。
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.api_endpoint import ApiEndpoint
from app.models.audit_log import AuditLog
from app.models.contract import Contract
from app.models.user import User, UserRole
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


async def _parties(db):
    provider, provider_headers = await _mk_user(db, UserRole.DATA_PROVIDER, "ss18ocprov")
    buyer, buyer_headers = await _mk_user(db, UserRole.BUYER, "ss18ocbuy")
    outsider, outsider_headers = await _mk_user(db, UserRole.BUYER, "ss18ocout")
    return provider, provider_headers, buyer, buyer_headers, outsider, outsider_headers


async def _mk_endpoint(db, provider, buyer, slug=None):
    contract = Contract(
        contract_no=f"CT-{uuid.uuid4().hex[:12]}",
        contract_type="data_usage",
        status="active",
        provider_id=provider.id,
        buyer_id=buyer.id,
        title="SS-18 调用明细测试合约",
        product_ids=[str(uuid.uuid4())],
        delivery_methods=["open_api"],
    )
    db.add(contract)
    await db.flush()
    endpoint = ApiEndpoint(
        owner_id=buyer.id,
        contract_id=contract.id,
        name="日报",
        slug=slug or f"report-{uuid.uuid4().hex[:8]}",
    )
    db.add(endpoint)
    await db.flush()
    return contract, endpoint


def _calls_url(endpoint_id):
    return f"/api/v1/deliveries/openapi/endpoints/{endpoint_id}/calls"


async def _log_call(db, endpoint, *, succeeded=True, user=None, ip="203.0.113.9", at=None):
    row = AuditLog(
        user_id=user.id if user else None,
        action="openapi.call" if succeeded else "openapi.call_failed",
        resource_type="api_endpoint",
        resource_id=str(endpoint.id),
        ip_address=ip,
        detail={
            "contract_id": str(endpoint.contract_id),
            "endpoint_id": str(endpoint.id),
            "slug": endpoint.slug,
        } if succeeded else {
            "contract_id": str(endpoint.contract_id),
            "endpoint_id": str(endpoint.id),
            "slug": endpoint.slug,
            "code": "QUOTA_EXCEEDED",
            "message": "配额已用尽",
        },
        created_at=at or datetime.now(timezone.utc),
    )
    db.add(row)
    await db.flush()
    return row


class TestCallAuditShape:
    async def test_success_call_records_source_ip_under_endpoint(self, client, db_session, monkeypatch):
        provider, _ph, buyer, headers, _o, _oh = await _parties(db_session)
        contract, endpoint = await _mk_endpoint(db_session, provider, buyer)

        async def _fake_execute(db, ep, body, *, caller_id, workload=None):
            return {"output_rows": 7, "duration_ms": 42, "request_sha256": "a" * 64, "data": []}

        monkeypatch.setattr(dv, "execute_endpoint", _fake_execute)
        resp = await client.post(f"/open/v1/{endpoint.slug}", json={"limit": 10}, headers=headers)
        assert resp.status_code == 200, resp.text

        row = (await db_session.execute(select(AuditLog).where(AuditLog.action == "openapi.call"))).scalar_one()
        assert row.resource_type == "api_endpoint"
        assert row.resource_id == str(endpoint.id)
        assert row.ip_address
        assert row.detail["contract_id"] == str(contract.id)
        assert row.detail["output_rows"] == 7

        listed = await client.get(_calls_url(endpoint.id), headers=headers)
        assert listed.status_code == 200, listed.text
        item = listed.json()["items"][0]
        assert item["status"] == "succeeded"
        assert item["code"] is None
        assert item["client_ip"] == row.ip_address
        assert item["output_rows"] == 7
        assert item["duration_ms"] == 42
        assert item["request_sha256"] == "a" * 64
        assert item["caller_id"] == str(buyer.id)

    async def test_refused_call_is_failed_without_zeroed_rows(self, client, db_session, monkeypatch):
        provider, _ph, buyer, headers, _o, _oh = await _parties(db_session)
        _contract, endpoint = await _mk_endpoint(db_session, provider, buyer)

        async def _refuse(db, ep, body, *, caller_id, workload=None):
            raise dv.EndpointUnavailable("QUOTA_EXCEEDED", "配额已用尽")

        monkeypatch.setattr(dv, "execute_endpoint", _refuse)
        resp = await client.post(f"/open/v1/{endpoint.slug}", json={}, headers=headers)
        assert resp.status_code == 429, resp.text
        assert resp.json()["code"] == "QUOTA_EXCEEDED"

        listed = await client.get(_calls_url(endpoint.id), headers=headers)
        body = listed.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["status"] == "failed"
        assert item["code"] == "QUOTA_EXCEEDED"
        assert item["output_rows"] is None
        assert item["duration_ms"] is None
        assert item["client_ip"]

    async def test_prechange_contract_scoped_rows_are_not_claimed(self, client, db_session):
        provider, _ph, buyer, headers, _o, _oh = await _parties(db_session)
        contract, endpoint = await _mk_endpoint(db_session, provider, buyer)
        db_session.add(AuditLog(
            user_id=buyer.id,
            action="openapi.call",
            resource_type="contract",
            resource_id=str(contract.id),
            detail={"endpoint_id": str(endpoint.id), "slug": endpoint.slug, "output_rows": 1},
            created_at=datetime.now(timezone.utc),
        ))
        await db_session.flush()

        listed = await client.get(_calls_url(endpoint.id), headers=headers)
        assert listed.status_code == 200, listed.text
        assert listed.json()["items"] == []
        assert listed.json()["total"] == 0


class TestCallQuery:
    async def test_status_filter_and_pagination_are_exact(self, client, db_session):
        provider, _ph, buyer, headers, _o, _oh = await _parties(db_session)
        _contract, endpoint = await _mk_endpoint(db_session, provider, buyer)
        base = datetime(2026, 10, 16, 12, 0, 0, tzinfo=timezone.utc)
        for i in range(3):
            await _log_call(db_session, endpoint, succeeded=True, at=base + timedelta(seconds=i))
        for i in range(2):
            await _log_call(db_session, endpoint, succeeded=False, at=base + timedelta(seconds=10 + i))

        everything = await client.get(_calls_url(endpoint.id), headers=headers)
        assert everything.json()["total"] == 5
        stamps = [datetime.fromisoformat(i["created_at"].replace("Z", "+00:00")) for i in everything.json()["items"]]
        assert stamps == sorted(stamps, reverse=True)
        assert [i["status"] for i in everything.json()["items"]] == ["failed", "failed", "succeeded", "succeeded", "succeeded"]

        failed = await client.get(_calls_url(endpoint.id) + "?status=failed", headers=headers)
        assert failed.json()["total"] == 2
        assert all(i["status"] == "failed" for i in failed.json()["items"])
        assert all(i["code"] == "QUOTA_EXCEEDED" for i in failed.json()["items"])

        page2 = await client.get(_calls_url(endpoint.id) + "?page=2&page_size=2", headers=headers)
        body = page2.json()
        assert body["total"] == 5 and body["page"] == 2
        assert [i["created_at"] for i in body["items"]] == [i["created_at"] for i in everything.json()["items"][2:4]]

    async def test_unknown_status_is_422(self, client, db_session):
        provider, _ph, buyer, headers, _o, _oh = await _parties(db_session)
        _contract, endpoint = await _mk_endpoint(db_session, provider, buyer)
        resp = await client.get(_calls_url(endpoint.id) + "?status=maybe", headers=headers)
        assert resp.status_code == 422, resp.text

    async def test_outsider_cannot_read_calls(self, client, db_session):
        provider, _ph, buyer, _h, outsider, outsider_headers = await _parties(db_session)
        _contract, endpoint = await _mk_endpoint(db_session, provider, buyer)
        await _log_call(db_session, endpoint, user=buyer)

        resp = await client.get(_calls_url(endpoint.id), headers=outsider_headers)
        assert resp.status_code in (403, 404), resp.text

    async def test_unknown_endpoint_is_404(self, client, db_session):
        _provider, _ph, _buyer, headers, _o, _oh = await _parties(db_session)
        resp = await client.get(_calls_url(uuid.uuid4()), headers=headers)
        assert resp.status_code == 404, resp.text
