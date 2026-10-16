"""SS-17 成果包历史列表 — GET /api/v1/deliveries/dataset/bundles。

钉住三件事：
1. 归属口径与下载端点**同源**（买方/提供方/admin，见 migration 0021 的 fail-closed 规则）：
   列表里能看见的包与能下载的包不允许出现两套口径。
2. 分页信封与 deliveries 里既有列表一致（items/total/page/page_size），且 total 是过滤后的总数。
3. 机器凭据（平台 API Key）按**签发人**的可见范围返回，不会因为换了凭据就看得见别人的包。
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.core import scopes as ss12_scopes
from app.models.api_key import ApiKey
from app.models.contract import Contract
from app.models.delivery_bundle import DeliveryBundle
from app.models.user import User, UserRole
from app.services.auth_service import create_access_token, hash_password
from app.utils.crypto import sm3_hash

pytestmark = pytest.mark.asyncio

BUNDLES_URL = "/api/v1/deliveries/dataset/bundles"


@pytest.fixture(autouse=True)
def _clean_key_state():
    """API Key 的爆破冷却/限流是进程内计数器，跨用例残留会让凭据用例假失败。"""
    ss12_scopes.reset_auth_counters()
    ss12_scopes.reset_rate_limits()
    yield
    ss12_scopes.reset_auth_counters()
    ss12_scopes.reset_rate_limits()


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


async def _mk_contract(db, provider, buyer):
    contract = Contract(
        contract_no=f"CT-{uuid.uuid4().hex[:12]}",
        contract_type="data_usage",
        status="active",
        provider_id=provider.id,
        buyer_id=buyer.id,
        title="SS-17 成果包测试合约",
        product_ids=[str(uuid.uuid4())],
    )
    db.add(contract)
    await db.flush()
    await db.refresh(contract)
    return contract


async def _mk_bundle(db, contract, *, file_count=3, size_bytes=2048):
    """只写归属记录：下载要的对象存储字节由下载用例自己负责，列表不碰存储。"""
    bundle = DeliveryBundle(
        id=uuid.uuid4(),
        contract_id=contract.id,
        session_id=None,
        created_by=contract.buyer_id,
        object_name=f"delivery/{uuid.uuid4()}.zip",
        size_bytes=size_bytes,
        file_count=file_count,
    )
    db.add(bundle)
    await db.flush()
    return bundle


async def _two_contracts(db):
    """两组完全独立的当事方：A 组（用于看得见）与 B 组（用于证明看不见）。"""
    provider_a, provider_a_headers = await _mk_user(db, UserRole.DATA_PROVIDER, "ss17provA")
    buyer_a, buyer_a_headers = await _mk_user(db, UserRole.BUYER, "ss17buyA")
    contract_a = await _mk_contract(db, provider_a, buyer_a)

    provider_b, _ = await _mk_user(db, UserRole.DATA_PROVIDER, "ss17provB")
    buyer_b, buyer_b_headers = await _mk_user(db, UserRole.BUYER, "ss17buyB")
    contract_b = await _mk_contract(db, provider_b, buyer_b)

    bundle_a = await _mk_bundle(db, contract_a, file_count=4, size_bytes=4096)
    bundle_b = await _mk_bundle(db, contract_b, file_count=1, size_bytes=512)
    return {
        "provider_a": provider_a,
        "provider_a_headers": provider_a_headers,
        "buyer_a": buyer_a,
        "buyer_a_headers": buyer_a_headers,
        "contract_a": contract_a,
        "buyer_b": buyer_b,
        "buyer_b_headers": buyer_b_headers,
        "contract_b": contract_b,
        "bundle_a": bundle_a,
        "bundle_b": bundle_b,
    }


class TestBundleListOwnership:
    async def test_buyer_sees_only_own_contract_bundles(self, client, db_session):
        ctx = await _two_contracts(db_session)

        resp = await client.get(BUNDLES_URL, headers=ctx["buyer_a_headers"])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        ids = [item["bundle_id"] for item in body["items"]]
        assert str(ctx["bundle_a"].id) in ids
        assert str(ctx["bundle_b"].id) not in ids

    async def test_provider_of_same_contract_sees_the_bundle(self, client, db_session):
        ctx = await _two_contracts(db_session)

        resp = await client.get(BUNDLES_URL, headers=ctx["provider_a_headers"])
        assert resp.status_code == 200, resp.text
        ids = [item["bundle_id"] for item in resp.json()["items"]]
        assert ids == [str(ctx["bundle_a"].id)]

    async def test_outsider_gets_empty_list_not_error(self, client, db_session):
        ctx = await _two_contracts(db_session)

        resp = await client.get(BUNDLES_URL, headers=ctx["buyer_b_headers"])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert [item["bundle_id"] for item in body["items"]] == [str(ctx["bundle_b"].id)]
        assert str(ctx["bundle_a"].id) not in resp.text

    async def test_admin_sees_all_bundles(self, client, db_session):
        ctx = await _two_contracts(db_session)
        _, admin_headers = await _mk_user(db_session, UserRole.ADMIN, "ss17admin")

        resp = await client.get(BUNDLES_URL, headers=admin_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 2
        assert {item["bundle_id"] for item in body["items"]} == {
            str(ctx["bundle_a"].id),
            str(ctx["bundle_b"].id),
        }

    async def test_list_and_download_share_one_ownership_rule(self, client, db_session):
        """列表看不见 == 下载不放行（两端都用 _is_party，口径不得漂移）。"""
        ctx = await _two_contracts(db_session)

        listing = await client.get(BUNDLES_URL, headers=ctx["buyer_b_headers"])
        assert str(ctx["bundle_a"].id) not in [i["bundle_id"] for i in listing.json()["items"]]

        denied = await client.get(
            f"/api/v1/deliveries/dataset/{ctx['bundle_a'].id}/download",
            headers=ctx["buyer_b_headers"],
        )
        assert denied.status_code == 403, denied.text


class TestBundleListShape:
    async def test_item_fields_are_the_persisted_columns_only(self, client, db_session):
        ctx = await _two_contracts(db_session)

        resp = await client.get(BUNDLES_URL, headers=ctx["buyer_a_headers"])
        item = resp.json()["items"][0]
        assert item["bundle_id"] == str(ctx["bundle_a"].id)
        assert item["contract_id"] == str(ctx["contract_a"].id)
        assert item["contract_no"] == ctx["contract_a"].contract_no
        assert item["session_id"] is None
        assert item["file_count"] == 4
        assert item["size_bytes"] == 4096
        assert item["download_url"] == f"/api/v1/deliveries/dataset/{ctx['bundle_a'].id}/download"
        # created_at 由 DB server_default 落值，只要求字段存在且非空（值本身不做断言）。
        assert item["created_at"] is not None
        # 水印标志只写在包内 MANIFEST.json，不在这个行上：后端不下发，前端也不猜。
        assert "watermark_applied" not in item

    async def test_filter_by_contract_id_narrows_total_too(self, client, db_session):
        ctx = await _two_contracts(db_session)
        extra = await _mk_bundle(db_session, ctx["contract_a"], file_count=2, size_bytes=10)

        resp = await client.get(
            BUNDLES_URL, params={"contract_id": str(ctx["contract_a"].id)}, headers=ctx["buyer_a_headers"]
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 2
        assert {item["bundle_id"] for item in body["items"]} == {str(ctx["bundle_a"].id), str(extra.id)}

        other = await client.get(
            BUNDLES_URL, params={"contract_id": str(ctx["contract_b"].id)}, headers=ctx["buyer_a_headers"]
        )
        assert other.status_code == 200, other.text
        # 过滤到别人合约上的包：空列表，而不是把 A 组的包漏出来。
        assert other.json()["items"] == []
        assert other.json()["total"] == 0

    async def test_pagination_pages_are_disjoint_and_cover_total(self, client, db_session):
        ctx = await _two_contracts(db_session)
        created = {str(ctx["bundle_a"].id)}
        for _ in range(2):
            created.add(str((await _mk_bundle(db_session, ctx["contract_a"])).id))

        page1 = await client.get(
            BUNDLES_URL, params={"page": 1, "page_size": 2}, headers=ctx["buyer_a_headers"]
        )
        page2 = await client.get(
            BUNDLES_URL, params={"page": 2, "page_size": 2}, headers=ctx["buyer_a_headers"]
        )
        assert page1.status_code == 200 and page2.status_code == 200
        first, second = page1.json(), page2.json()

        assert first["total"] == 3 and first["page"] == 1 and first["page_size"] == 2
        assert second["total"] == 3 and second["page"] == 2
        assert len(first["items"]) == 2 and len(second["items"]) == 1
        ids1 = {item["bundle_id"] for item in first["items"]}
        ids2 = {item["bundle_id"] for item in second["items"]}
        assert not (ids1 & ids2)
        assert ids1 | ids2 == created

    async def test_page_size_over_limit_is_rejected(self, client, db_session):
        ctx = await _two_contracts(db_session)
        resp = await client.get(BUNDLES_URL, params={"page_size": 101}, headers=ctx["buyer_a_headers"])
        assert resp.status_code == 422, resp.text

    async def test_requires_authentication(self, client, db_session):
        await _two_contracts(db_session)
        resp = await client.get(BUNDLES_URL)
        assert resp.status_code in (401, 403), resp.text


class TestBundleListWithMachineCredential:
    async def _mint_key(self, db, issuer, scopes):
        bare = ApiKey.generate_bare_key()
        now = datetime.now(timezone.utc)
        db.add(
            ApiKey(
                name="ss17-key",
                key_hash=sm3_hash(bare.encode()),
                key_prefix=ApiKey.display_prefix(bare),
                scopes=list(scopes),
                status="active",
                rate_limit=120,
                issued_by=issuer.id,
                created_at=now,
                updated_at=now,
            )
        )
        await db.flush()
        return bare

    async def test_key_sees_issuers_scope_not_more(self, client, db_session):
        """买方 key 只能看见签发人自己作为当事方的合约——换凭据不扩大可见范围。"""
        ctx = await _two_contracts(db_session)
        bare = await self._mint_key(db_session, ctx["buyer_a"], ["gateway:read"])

        resp = await client.get(BUNDLES_URL, headers={"Authorization": f"Bearer {bare}"})
        assert resp.status_code == 200, resp.text
        ids = [item["bundle_id"] for item in resp.json()["items"]]
        assert ids == [str(ctx["bundle_a"].id)]

    async def test_key_without_gateway_read_is_denied(self, client, db_session):
        """/api/v1/deliveries 属 gateway 组：作用域不匹配的 key 一律 API_KEY_SCOPE_DENIED。"""
        ctx = await _two_contracts(db_session)
        bare = await self._mint_key(db_session, ctx["buyer_a"], ["data:read"])

        resp = await client.get(BUNDLES_URL, headers={"Authorization": f"Bearer {bare}"})
        assert resp.status_code == 403, resp.text
        assert resp.json()["code"] == "API_KEY_SCOPE_DENIED"
