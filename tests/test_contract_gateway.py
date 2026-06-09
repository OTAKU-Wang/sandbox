"""Contract Execution Gateway tests — auth, authz, content security, metering."""
import uuid
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

from app.services.gateway_service import (
    GatewayService, GatewayRequest, GatewayResponse, MeteringRecord, sm3_hash,
)
from app.models.app_credential import AppCredential, CredentialStatus
from app.models.contract import Contract, ContractStatus


# === SM3 Hash ===

def test_sm3_hash_deterministic():
    """Same input produces same hash."""
    h1 = sm3_hash("test_secret")
    h2 = sm3_hash("test_secret")
    assert h1 == h2
    assert len(h1) == 64  # 256-bit hex


def test_sm3_hash_different_inputs():
    """Different inputs produce different hashes."""
    assert sm3_hash("secret1") != sm3_hash("secret2")


# === AppCredential Generation ===

def test_generate_app_id():
    app_id = AppCredential.generate_app_id()
    assert app_id.startswith("app_")
    assert len(app_id) == 36  # "app_" + 32 hex chars


def test_generate_app_secret():
    secret = AppCredential.generate_app_secret()
    assert len(secret) == 64  # 32 bytes = 64 hex chars


def test_generate_unique_ids():
    ids = {AppCredential.generate_app_id() for _ in range(100)}
    assert len(ids) == 100  # All unique


# === Content Security ===

def test_redact_phone_number():
    svc = GatewayService()
    text, detected = svc._redact_pii("联系人：13812345678")
    assert detected is True
    assert "13812345678" not in text
    assert "1**********" in text


def test_redact_email():
    svc = GatewayService()
    text, detected = svc._redact_pii("邮箱：test@example.com")
    assert detected is True
    assert "test@example.com" not in text
    assert "***@" in text


def test_redact_id_card():
    svc = GatewayService()
    text, detected = svc._redact_pii("身份证：110101199001011234")
    assert detected is True
    assert "110101199001011234" not in text


def test_no_pii_unchanged():
    svc = GatewayService()
    text, detected = svc._redact_pii("普通文本，没有敏感信息")
    assert detected is False
    assert text == "普通文本，没有敏感信息"


def test_apply_content_security():
    svc = GatewayService()

    class MockContract:
        id = uuid.uuid4()
        buyer_id = uuid.uuid4()

    data = {
        "columns": ["name", "phone"],
        "rows": [
            ["Alice", "13812345678"],
            ["Bob", "13900001111"],
        ],
        "row_count": 2,
    }

    secured, report = svc.apply_content_security(data, MockContract())

    assert report["pii_detected"] is True
    assert report["pii_redacted"] is True
    assert report["watermark_applied"] is True
    assert "_watermark" in secured
    # Phone numbers should be redacted
    for row in secured["rows"]:
        assert "13812345678" not in str(row)
        assert "13900001111" not in str(row)


def test_apply_content_security_no_pii():
    svc = GatewayService()

    class MockContract:
        id = uuid.uuid4()
        buyer_id = uuid.uuid4()

    data = {
        "columns": ["id", "value"],
        "rows": [["1", "100"], ["2", "200"]],
        "row_count": 2,
    }

    secured, report = svc.apply_content_security(data, MockContract())
    assert report["pii_detected"] is False
    assert report["watermark_applied"] is True


@pytest.mark.asyncio
async def test_query_duckdb_loads_encrypted_csv_storage():
    from app.services.storage_service import storage_service

    svc = GatewayService()
    csv_data = b"id,name\n1,Alice\n2,Bob\n"
    upload = storage_service.upload(csv_data, f"gateway/{uuid.uuid4()}/customers.csv")
    product = MagicMock()
    product.id = uuid.uuid4()
    product.name = "customers"
    product.encrypted_storage_path = upload["path"]

    response = await svc._query_duckdb(product, "SELECT id, name FROM customers WHERE id = '1'", "json")

    assert response.success is True
    assert response.data["columns"] == ["id", "name"]
    assert response.data["rows"] == [["1", "Alice"]]


# === GatewayResponse ===

def test_gateway_response_success():
    resp = GatewayResponse(success=True, data={"rows": []})
    assert resp.success is True
    assert resp.status_code == 200


def test_gateway_response_error():
    resp = GatewayResponse(success=False, error="Not found", status_code=404)
    assert resp.success is False
    assert resp.error == "Not found"
    assert resp.status_code == 404


# === MeteringRecord ===

def test_metering_record():
    record = MeteringRecord(
        request_id="req-1",
        contract_id="c-1",
        app_id="app-1",
        consumer_id="u-1",
        product_id="p-1",
        operation="query",
        rows_returned=100,
        bytes_returned=5000,
        duration_ms=150,
        status_code=200,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    assert record.request_id == "req-1"
    assert record.rows_returned == 100
    assert record.bytes_returned == 5000


# === CredentialStatus ===

def test_credential_status_values():
    assert CredentialStatus.ACTIVE.value == "active"
    assert CredentialStatus.SUSPENDED.value == "suspended"
    assert CredentialStatus.REVOKED.value == "revoked"
    assert CredentialStatus.EXPIRED.value == "expired"


# === ContractStatus ===

def test_contract_status_active():
    assert ContractStatus.ACTIVE.value == "active"
    assert ContractStatus.SIGNED.value == "signed"
