"""OCR-A13 / OCR-A161 / OCR-A271 — DEK 生命周期只认「已登录」，而密钥库是进程内存。

A13（critical）：`app/api/kms.py` 全部路由只挂 `get_current_user` ⇒ 任意登录账号
（数商/买方）都能建 DEK、轮换乘别人的 DEK、吊销任意 DEK（连带终止使用它的会话），
`GET /keys` 还是全库 DEK 元数据且无 limit；`ticket_id` 只进审计不作校验。
A161（critical）：KMS 密钥库是进程内存，恢复器只读 `wrapped_payload`，而
`create_dek`/`rotate_dek` 只写 `encrypted_key` ⇒ 行在、密钥没了，按该 key_id 加密
的落库数据重启后永久不可解（写路径还静默成功）。
A271（low，后端侧）：`GET /keys` 的条目没有 `algorithm`/`key_type`，逼得前端
`normalizeKmsKey` 用 `algorithm || 'SM4'` 编一个算法名出来。

角色口径取自随货前端的守卫（cds-frontend/src/utils/roles.ts）：
密钥管理页 = identityManagers = [operator, admin]；审计读 = auditReaders =
[operator, regulator, admin]。收紧到这两组，随货 UI 不受影响。
"""
import uuid

import pytest
from sqlalchemy import func, select

from app.models.kms import DataEncryptionKey
from app.services.kms_recovery import restore_wrapped_keys
from app.services.kms_service import kms_service

pytestmark = pytest.mark.asyncio


async def _create_key(client, headers, product_id=None) -> dict:
    resp = await client.post(
        "/api/v1/kms/keys",
        params={"product_id": str(product_id or uuid.uuid4())},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ─── A13: 越权面 ─────────────────────────────────────────────────────────────

async def test_non_platform_roles_cannot_create_deks(client, make_user, db_session):
    """建 DEK 是平台侧密管操作：数商/买方必须 403，且不留行。"""
    buyer, _ = await make_user("buyer", "a13buy")
    provider, _ = await make_user("data_provider", "a13prov")
    product_id = uuid.uuid4()
    before = (await db_session.execute(
        select(func.count()).select_from(DataEncryptionKey)
        .where(DataEncryptionKey.product_id == product_id)
    )).scalar_one()

    for headers in (buyer, provider):
        resp = await client.post(
            "/api/v1/kms/keys", params={"product_id": str(product_id)}, headers=headers,
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["code"] == "HTTP_403"

    after = (await db_session.execute(
        select(func.count()).select_from(DataEncryptionKey)
        .where(DataEncryptionKey.product_id == product_id)
    )).scalar_one()
    assert after == before, "被拒的请求不得留下 DEK 行"


async def test_non_platform_roles_cannot_read_or_destroy_keys(
    client, admin_headers, make_user, db_session,
):
    """列举/详情/轮换/吊销/密钥审计都不是「登录即可」（A13：含破坏性操作）。"""
    created = await _create_key(client, admin_headers)
    key_id = created["key_id"]
    buyer, _ = await make_user("buyer", "a13buy2")

    checks = [
        ("get", await client.get(f"/api/v1/kms/keys/{key_id}", headers=buyer)),
        ("list", await client.get("/api/v1/kms/keys", headers=buyer)),
        ("rotate", await client.post(f"/api/v1/kms/keys/{key_id}/rotate", headers=buyer)),
        ("revoke", await client.request(
            "DELETE", f"/api/v1/kms/keys/{key_id}",
            json={"reason": "越权尝试"}, headers=buyer,
        )),
        ("audit", await client.get("/api/v1/kms/audit", params={"key_id": key_id}, headers=buyer)),
    ]
    for name, resp in checks:
        assert resp.status_code == 403, f"{name}: {resp.status_code} {resp.text}"
        assert resp.json()["code"] == "HTTP_403"

    # 密钥必须仍是 ACTIVE：被拒的吊销/轮换不得已经生效
    row = (await db_session.execute(
        select(DataEncryptionKey).where(DataEncryptionKey.key_id == key_id)
    )).scalar_one()
    assert row.status == "active"


async def test_platform_roles_keep_working(client, admin_headers, operator_headers, make_user):
    """防过度修正：运营/管理员的既有流程必须照旧可用。"""
    created = await _create_key(client, operator_headers)
    key_id = created["key_id"]

    listed = await client.get("/api/v1/kms/keys", headers=operator_headers)
    assert listed.status_code == 200
    assert key_id in [item["key_id"] for item in listed.json()["items"]]

    detail = await client.get(f"/api/v1/kms/keys/{key_id}", headers=admin_headers)
    assert detail.status_code == 200

    rotated = await client.post(f"/api/v1/kms/keys/{key_id}/rotate", headers=operator_headers)
    assert rotated.status_code == 200, rotated.text

    audit = await client.get("/api/v1/kms/audit", params={"key_id": key_id}, headers=operator_headers)
    assert audit.status_code == 200
    assert {e["operation"] for e in audit.json()["items"]} >= {"create"}


async def test_key_audit_is_readable_by_regulator_but_keys_are_not(client, admin_headers, make_user):
    """密钥审计属监管可见面（同 auditReaders）；DEK 台账不是。"""
    await _create_key(client, admin_headers)
    regulator, _ = await make_user("regulator", "a13reg")

    audit = await client.get("/api/v1/kms/audit", headers=regulator)
    assert audit.status_code == 200, audit.text

    listed = await client.get("/api/v1/kms/keys", headers=regulator)
    assert listed.status_code == 403


async def test_dek_list_is_bounded_and_reports_total(client, admin_headers):
    """A13：`GET /keys` 无 limit ⇒ 全库 DEK 元数据一次性吐完。"""
    first = await _create_key(client, admin_headers)
    second = await _create_key(client, admin_headers)

    resp = await client.get("/api/v1/kms/keys", params={"limit": 1}, headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1, "limit 必须生效"
    assert body["total"] >= 2, "截断必须可见（total 给全量）"
    assert {first["key_id"], second["key_id"]} - {i["key_id"] for i in body["items"]}


# ─── A161: 写路径不得只存 key_id ─────────────────────────────────────────────

async def test_created_dek_survives_keystore_restart(client, admin_headers, db_session):
    """恢复器只读 `wrapped_payload`；只写 `encrypted_key` 的行等于把密文判死刑。"""
    created = await _create_key(client, admin_headers)
    key_id = created["key_id"]
    plaintext = kms_service.get_key(key_id)
    wrapped = kms_service.export_wrapped(key_id)
    assert wrapped is not None and plaintext is not None

    row = (await db_session.execute(
        select(DataEncryptionKey).where(DataEncryptionKey.key_id == key_id)
    )).scalar_one()
    assert row.wrapped_payload == wrapped, (
        "create_dek 必须把 KEK-wrapped blob 同时写进 wrapped_payload，"
        "否则 kms_recovery.restore_wrapped_keys 根本不看这一行"
    )

    # 模拟 api 重启：内存密钥库清空（KEK 在 HSM/Vault 侧，与重启无关）
    kms_service._wrapped_keys.pop(key_id, None)
    kms_service._sm2_encrypted_keys.pop(key_id, None)
    assert kms_service.get_key(key_id) is None

    restored = await restore_wrapped_keys(db_session)
    assert restored >= 1
    assert kms_service.get_key(key_id) == plaintext, "重启后必须还能解开按该 key_id 加密的数据"


async def test_rotated_dek_survives_keystore_restart(client, admin_headers, db_session):
    created = await _create_key(client, admin_headers)
    rotated = await client.post(f"/api/v1/kms/keys/{created['key_id']}/rotate", headers=admin_headers)
    assert rotated.status_code == 200
    new_key_id = rotated.json()["new_key_id"]

    row = (await db_session.execute(
        select(DataEncryptionKey).where(DataEncryptionKey.key_id == new_key_id)
    )).scalar_one()
    assert row.wrapped_payload == kms_service.export_wrapped(new_key_id)


async def test_create_dek_fails_closed_without_wrapped_blob(client, admin_headers, db_session, monkeypatch):
    """没有可用持久化材料时必须显式失败，绝不静默落一行「ACTIVE 但永远解不开」。"""
    product_id = uuid.uuid4()
    monkeypatch.setattr(kms_service, "export_wrapped", lambda key_id: None)

    resp = await client.post(
        "/api/v1/kms/keys", params={"product_id": str(product_id)}, headers=admin_headers,
    )
    assert resp.status_code == 503, resp.text
    assert resp.json()["code"] == "KMS_UNAVAILABLE"

    rows = (await db_session.execute(
        select(DataEncryptionKey).where(DataEncryptionKey.product_id == product_id)
    )).scalars().all()
    assert rows == [], "写路径失败不得留下 DEK 行"


async def test_create_dek_fails_closed_when_new_version_has_no_blob(client, admin_headers, monkeypatch):
    created = await _create_key(client, admin_headers)
    key_id = created["key_id"]

    real_export = kms_service.export_wrapped

    def _only_old(key_id_arg):
        return real_export(key_id_arg) if key_id_arg == key_id else None

    monkeypatch.setattr(kms_service, "export_wrapped", _only_old)
    resp = await client.post(f"/api/v1/kms/keys/{key_id}/rotate", headers=admin_headers)
    assert resp.status_code == 503, resp.text
    assert resp.json()["code"] == "KMS_UNAVAILABLE"


# ─── A161: 内存态必须可见，不得只在解密时才发现 ──────────────────────────────

async def test_memory_only_dek_keys_are_introspectable():
    """generate_data_key 造的密钥若不落 wrapped blob 就是重启即失；状态要能查出来。"""
    key_id = kms_service.generate_data_key(f"a161-probe-{uuid.uuid4().hex[:6]}")["key_id"]
    try:
        assert kms_service.is_persisted(key_id) is False
        assert key_id in kms_service.unpersisted_key_ids()

        # 调用方把 blob 落库后（同事务写 KeyMetadata.wrapped_payload）才叫可存活
        kms_service.mark_persisted(key_id)
        assert kms_service.is_persisted(key_id) is True
        assert key_id not in kms_service.unpersisted_key_ids()
    finally:
        kms_service.destroy_key(key_id)
        assert key_id not in kms_service.unpersisted_key_ids()


async def test_session_keys_stay_ephemeral_by_design():
    """会话密钥按设计不落盘（终止即 crypto-erase），不得混进「待持久化」清单。"""
    key_id = kms_service.generate_session_key(f"a161-sess-{uuid.uuid4().hex[:6]}")["key_id"]
    try:
        assert key_id not in kms_service.unpersisted_key_ids()
        assert kms_service.is_persisted(key_id) is False
    finally:
        kms_service.destroy_key(key_id)


# ─── A271（后端侧）: 别再逼前端编算法名 ──────────────────────────────────────

async def test_key_list_items_carry_algorithm_and_key_type(client, admin_headers):
    """前端 normalizeKmsKey 的 `algorithm || 'SM4'` 之所以存在，是因为条目里没这两个字段。"""
    created = await _create_key(client, admin_headers)
    resp = await client.get("/api/v1/kms/keys", headers=admin_headers)
    assert resp.status_code == 200
    item = next(i for i in resp.json()["items"] if i["key_id"] == created["key_id"])
    assert item["algorithm"] == created["algorithm"]
    assert item["key_type"] == "dek"
    assert item["persisted"] is True, "持久化状态要在台账里可见（A161 的可核验面）"
