"""OCR-A1 / OCR-A106 — 任意已认证用户可导出全部 SM2 私钥；验链结论名不副实。

A1（critical）：`GET /api/v1/certificates/{cert_id}` 返回 `CertDetailResponse`，
里面带着 `key_pem`（SM2 私钥明文）——`get_certificate` 不做任何归属/角色校验，
所以一个买方账号遍历 8 位 cert_id 就能把平台全部 TLS 私钥拖走。
修法按台账：详情不再带私钥；导出私钥独立成端点 + 仅 ADMIN + 落审计。

A106（潜伏）：`GET /{cert_id}/verify-chain` 直接把 `certificate_service.verify_chain`
的结论当「信任链已验证」发布，而那个函数只走 DB 行、比对 issuer/subject 字符串，
既不验签名也没有可信锚；`POST /verify` 同样只查有效期。根因在
`app/services/certificate_service.py`（不在本任务文件集），本层先做到**不撒谎**：
结论必须自带 assurance 说明它到底验了什么。
"""
import uuid

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog

pytestmark = pytest.mark.asyncio


async def _generate(client, headers, subject: str) -> str:
    resp = await client.post("/api/v1/certificates/generate", json={
        "subject": subject, "cert_type": "signing", "validity_days": 30,
    }, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["cert_id"]


# ─── A1: 私钥导出面 ──────────────────────────────────────────────────────────

async def test_cert_detail_does_not_leak_the_private_key(client, operator_headers, make_user):
    """任意已认证用户读详情不得拿到 key_pem（A1 的拖库路径）。"""
    cert_id = await _generate(client, operator_headers, f"A1 {uuid.uuid4().hex[:8]}")
    buyer, _ = await make_user("buyer", "a1buy")

    resp = await client.get(f"/api/v1/certificates/{cert_id}", headers=buyer)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "key_pem" not in body, f"详情载荷仍在下发私钥字段: {sorted(body)}"
    # 公钥证书本身是公开材料，必须照常可读（前端详情抽屉依赖它）
    assert body["cert_pem"].startswith("-----BEGIN")
    assert body["cert_id"] == cert_id


async def test_certificate_list_never_carries_key_material(client, operator_headers, make_user):
    await _generate(client, operator_headers, f"A1 list {uuid.uuid4().hex[:8]}")
    buyer, _ = await make_user("buyer", "a1list")

    resp = await client.get("/api/v1/certificates/list", headers=buyer)
    assert resp.status_code == 200
    assert resp.json(), "列表不应为空"
    for item in resp.json():
        assert "key_pem" not in item
        assert "cert_pem" not in item


async def test_private_key_export_is_admin_only(client, operator_headers, admin_headers, make_user):
    """导出私钥 = 独立端点 + 仅 ADMIN（OPERATOR 也不给：批量导出等于拖库）。"""
    cert_id = await _generate(client, operator_headers, f"A1 export {uuid.uuid4().hex[:8]}")
    buyer, _ = await make_user("buyer", "a1exp")

    for headers, who in ((buyer, "buyer"), (operator_headers, "operator")):
        resp = await client.get(f"/api/v1/certificates/{cert_id}/private-key", headers=headers)
        assert resp.status_code == 403, f"{who}: {resp.status_code} {resp.text}"
        assert resp.json()["code"] == "HTTP_403"

    ok = await client.get(f"/api/v1/certificates/{cert_id}/private-key", headers=admin_headers)
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["cert_id"] == cert_id
    assert body["key_pem"].startswith("-----BEGIN"), "合法角色必须真的拿得到（防过度修正）"


async def test_private_key_export_is_audited(client, operator_headers, admin_headers, db_session):
    """私钥出库属高敏读取，必须留痕（同 certificate.revoke 的审计口径）。"""
    cert_id = await _generate(client, operator_headers, f"A1 audit {uuid.uuid4().hex[:8]}")
    await client.get(f"/api/v1/certificates/{cert_id}/private-key", headers=admin_headers)

    rows = (await db_session.execute(
        select(AuditLog).where(AuditLog.action == "certificate.export_key")
    )).scalars().all()
    assert [r.resource_id for r in rows] == [cert_id]


async def test_private_key_export_unknown_cert_is_404(client, admin_headers):
    resp = await client.get(f"/api/v1/certificates/{uuid.uuid4().hex[:8]}/private-key",
                            headers=admin_headers)
    assert resp.status_code == 404


# ─── A106: 验链结论必须说清自己验了什么 ──────────────────────────────────────

async def test_verify_endpoint_discloses_no_signature_check(client, operator_headers):
    """`valid: true` 目前只代表「解析成功 + 在有效期内」，一个字节的签名都没验。"""
    cert_id = await _generate(client, operator_headers, f"A106 {uuid.uuid4().hex[:8]}")
    pem = (await client.get(f"/api/v1/certificates/{cert_id}", headers=operator_headers)).json()

    resp = await client.post("/api/v1/certificates/verify", params={"cert_pem": pem["cert_pem"]},
                             headers=operator_headers)
    assert resp.status_code == 200, resp.text
    assurance = resp.json()["assurance"]
    assert assurance["signature_verified"] is False
    assert assurance["trust_anchor"] == "none"


async def test_verify_chain_discloses_no_signature_or_anchor(client, admin_headers):
    """verify_chain 只走 DB 行比对 issuer/subject 字符串；结论不得读作「链已验签」。

    这里故意不 seed 真行：`certificate_service.py:492` 的 `cert.not_after < now` 在
    SQLite 上必炸（naive vs aware datetime）——那是 A106 尚开的根因，在本任务
    文件集外，不得把它当成本层的验收点。未知 id 这条路径不碰日期，同样能
    展示“结论 = 查表结果”，并且 assurance 披露必须随结论一同发出。
    """
    resp = await client.get(f"/api/v1/certificates/{uuid.uuid4()}/verify-chain", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["valid"] is False and "not found" in body["error"], (
        "所调验链的全部依据就是一行 DB 记录——没有任何密码学输入参与"
    )
    assurance = body["assurance"]
    assert assurance["signature_verified"] is False
    assert assurance["trust_anchor"] == "none"
    assert assurance["revocation_checked_against"] == "database-row-status"


async def test_verify_chain_rejects_non_uuid_without_disclosure(client, admin_headers):
    resp = await client.get("/api/v1/certificates/not-a-uuid/verify-chain", headers=admin_headers)
    assert resp.status_code == 400
