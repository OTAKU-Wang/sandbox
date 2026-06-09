"""Tests for contract execution gateway — Task #137."""
import json
import uuid
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from app.models.app_credential import AppCredential, CredentialStatus
from app.services.gateway_service import sm3_hash, GatewayService, MeteringRecord


class TestAppCredential:
    """Test app credential model."""

    def test_generate_app_id(self):
        app_id = AppCredential.generate_app_id()
        assert app_id.startswith("app_")
        assert len(app_id) == 36  # "app_" + 32 hex chars

    def test_generate_app_secret(self):
        secret = AppCredential.generate_app_secret()
        assert len(secret) == 64  # 32 bytes = 64 hex chars

    def test_unique_ids(self):
        ids = {AppCredential.generate_app_id() for _ in range(100)}
        assert len(ids) == 100  # All unique


class TestSM3Hash:
    """Test SM3 hashing for app_secret."""

    def test_hash_deterministic(self):
        h1 = sm3_hash("test-secret")
        h2 = sm3_hash("test-secret")
        assert h1 == h2

    def test_hash_different_inputs(self):
        h1 = sm3_hash("secret-1")
        h2 = sm3_hash("secret-2")
        assert h1 != h2

    def test_hash_nonempty(self):
        h = sm3_hash("any-secret")
        assert len(h) > 0


class TestGatewayService:
    """Test gateway service core logic."""

    def test_redact_pii_phone(self):
        svc = GatewayService()
        text, detected = svc._redact_pii("Call me at 13812345678")
        assert detected is True
        assert "13812345678" not in text
        assert "1**********" in text

    def test_redact_pii_email(self):
        svc = GatewayService()
        text, detected = svc._redact_pii("Email: user@example.com")
        assert detected is True
        assert "user@example.com" not in text

    def test_redact_pii_id_card(self):
        svc = GatewayService()
        text, detected = svc._redact_pii("ID: 110101199001011234")
        assert detected is True
        assert "110101199001011234" not in text

    def test_redact_pii_no_pii(self):
        svc = GatewayService()
        text, detected = svc._redact_pii("Hello world 12345")
        assert detected is False
        assert text == "Hello world 12345"

    def test_content_security_watermark(self):
        svc = GatewayService()
        data = {
            "columns": ["id", "name"],
            "rows": [["1", "Alice"], ["2", "Bob"]],
        }

        mock_contract = MagicMock()
        mock_contract.id = uuid.uuid4()
        mock_contract.buyer_id = uuid.uuid4()

        secured, report = svc.apply_content_security(data, mock_contract)
        assert report["watermark_applied"] is True
        assert "_watermark" in secured

    def test_content_security_pii_redaction(self):
        svc = GatewayService()
        data = {
            "columns": ["id", "phone"],
            "rows": [["1", "13812345678"], ["2", "13987654321"]],
        }

        mock_contract = MagicMock()
        mock_contract.id = uuid.uuid4()
        mock_contract.buyer_id = uuid.uuid4()

        secured, report = svc.apply_content_security(data, mock_contract)
        assert report["pii_detected"] is True
        assert report["pii_redacted"] is True
        # Verify PII is redacted
        for row in secured["rows"]:
            assert "13812345678" not in str(row)
            assert "13987654321" not in str(row)


class TestMeteringRecord:
    """Test metering record data class."""

    def test_metering_record_fields(self):
        record = MeteringRecord(
            request_id="req-1",
            contract_id="contract-1",
            app_id="app-123",
            consumer_id="user-1",
            product_id="prod-1",
            operation="query",
            rows_returned=100,
            bytes_returned=1024,
            duration_ms=45,
            status_code=200,
        )
        assert record.rows_returned == 100
        assert record.bytes_returned == 1024
        assert record.operation == "query"


class TestClickHouseSchema:
    """Test gateway metering schema."""

    def test_schema_has_gateway_metering(self):
        from app.services.clickhouse_schema import clickhouse_schema
        tables = clickhouse_schema.list_tables()
        assert "gateway_metering" in tables

    def test_schema_columns(self):
        from app.services.clickhouse_schema import clickhouse_schema
        columns = clickhouse_schema.get_columns("gateway_metering")
        assert "request_id" in columns
        assert "contract_id" in columns
        assert "app_id" in columns
        assert "rows_returned" in columns
        assert "bytes_returned" in columns

    def test_generate_create_sql(self):
        from app.services.clickhouse_schema import clickhouse_schema
        sql = clickhouse_schema.generate_create_sql("gateway_metering")
        assert "SummingMergeTree()" in sql
        assert "gateway_metering" in sql
