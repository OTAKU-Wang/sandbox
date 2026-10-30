"""A51 — 解密查询（明文体出口）必须是特权面，普通会话拥有者不得经 HTTP 取明文。

台账：ocr-verified-findings.md A51（DEK 只在 ADMIN/OPERATOR 下发放 + 留痕）。
契约：W2 {code,message,detail,request_id}，拒绝码 FORBIDDEN / HTTP 403。
"""
import json
import uuid

import pytest

from app.services.audit_service import audit_service

DB = "/api/v1/sandbox-db"
PLAINTEXT = "110101199001011234"


@pytest.fixture
def audit_spy(monkeypatch):
    calls: list[dict] = []

    async def _log_standalone(**kwargs):
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(audit_service, "log_standalone", _log_standalone)
    return calls


async def _encrypted_table(client, headers, sid: str = "") -> str:
    sid = sid or f"a51-{uuid.uuid4().hex[:8]}"
    resp = await client.post(
        f"{DB}/create-encrypted-table",
        json={"session_id": sid, "table_name": "secret",
              "data": [{"name": "alice", "id_no": PLAINTEXT}],
              "encrypted_columns": {"id_no": "sm4-gcm"}},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return sid


@pytest.mark.asyncio
async def test_data_provider_cannot_pull_plaintext_over_http(client, make_user, audit_spy):
    """A51：DATA_PROVIDER 会话拥有者对 /query-decrypted 只能拿到 403 + W2 语义码，且响应无明文。"""
    headers, _uid = await make_user("data_provider", "a51dp")
    sid = await _encrypted_table(client, headers)

    resp = await client.post(
        f"{DB}/query-decrypted",
        json={"session_id": sid, "sql": "SELECT * FROM secret"},
        headers=headers,
    )
    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert body["code"] == "FORBIDDEN", body
    assert body["request_id"]
    assert PLAINTEXT not in resp.text
    assert [c.get("action") for c in audit_spy] == ["sandbox_db.decrypted_query_denied"]


@pytest.mark.asyncio
async def test_data_provider_cannot_read_dek(client, make_user):
    """A51：列级 DEK 仍只对 ADMIN/OPERATOR 发放；拥有者本人的 data_provider 也 403。"""
    headers, _uid = await make_user("data_provider", "a51dek")
    sid = await _encrypted_table(client, headers)

    resp = await client.get(f"{DB}/sessions/{sid}/encryption-config", headers=headers)
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] in ("FORBIDDEN", "HTTP_403")


@pytest.mark.asyncio
async def test_operator_decrypted_query_still_returns_plaintext(client, operator_headers):
    """A51 的反面：ADMIN/OPERATOR 的解密查询照常返回明文（不过度收紧）。"""
    sid = await _encrypted_table(client, operator_headers)

    resp = await client.post(
        f"{DB}/query-decrypted",
        json={"session_id": sid, "sql": "SELECT * FROM secret"},
        headers=operator_headers,
    )
    assert resp.status_code == 200, resp.text
    assert PLAINTEXT in resp.text


@pytest.mark.asyncio
async def test_encryption_metadata_carries_no_key_material(client, make_user):
    """A51：归属内的元数据端点只回列名/算法/key_id，任何密钥字节都不能出现。"""
    headers, _uid = await make_user("data_provider", "a51meta")
    sid = await _encrypted_table(client, headers)

    resp = await client.get(f"{DB}/sessions/{sid}/encryption-metadata", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["encrypted_tables"], body
    assert "dek_hex" not in json.dumps(body, default=str)
