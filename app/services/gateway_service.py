"""Contract Execution Gateway — unified data product access control.

Handles the full request lifecycle:
  AuthN → AuthZ → Route → Execute → Content Security → Meter → Audit

Architecture:
  Consumer → Gateway API → [Auth] → [OPA] → [Router] → Data Product
                                ↓         ↓         ↓
                            Credential  Policy   DuckDB/MinIO
                                ↓         ↓         ↓
                            Metering   Content   Audit Log
"""
import hashlib
import csv
import io
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_credential import AppCredential, CredentialStatus
from app.models.contract import Contract, ContractStatus
from app.models.data_product import DataProduct
from app.services.audit_service import audit_service
from app.services.quota_manager import quota_manager

logger = logging.getLogger(__name__)


@dataclass
class GatewayRequest:
    """Parsed gateway request."""
    contract_id: str
    app_id: str
    app_secret: str
    operation: str  # "query", "access", "export"
    sql: str | None = None
    target_product_id: str | None = None
    format: str = "json"
    client_ip: str = ""


@dataclass
class GatewayResponse:
    """Gateway response."""
    success: bool
    data: dict | list | None = None
    error: str | None = None
    status_code: int = 200
    metering: dict = field(default_factory=dict)
    content_security: dict = field(default_factory=dict)


@dataclass
class MeteringRecord:
    """Metering record for a gateway request."""
    request_id: str = ""
    contract_id: str = ""
    app_id: str = ""
    consumer_id: str = ""
    product_id: str = ""
    operation: str = ""
    rows_returned: int = 0
    bytes_returned: int = 0
    duration_ms: int = 0
    status_code: int = 200
    timestamp: str = ""


def sm3_hash(data: str) -> str:
    """SM3 hash for app_secret verification."""
    try:
        from gmssl import sm3, func as sm3_func
        data_bytes = data.encode("utf-8")
        hash_hex = sm3.sm3_hash(sm3_func.bytes_to_list(data_bytes))
        return hash_hex
    except ImportError:
        # Fallback to SHA-256 if gmssl not available
        return hashlib.sha256(data.encode()).hexdigest()


class GatewayService:
    """Contract execution gateway service."""

    def __init__(self):
        self._clickhouse_client = None

    # === Authentication ===

    async def authenticate(self, db: AsyncSession, app_id: str, app_secret: str,
                           client_ip: str = "") -> tuple[AppCredential | None, str | None]:
        """Authenticate an application via app_id + app_secret.

        Returns (credential, error_message).
        """
        result = await db.execute(
            select(AppCredential).where(AppCredential.app_id == app_id)
        )
        cred = result.scalar_one_or_none()

        if not cred:
            return None, "Invalid app_id"

        if cred.status != CredentialStatus.ACTIVE.value:
            return None, f"Credential is {cred.status}"

        if cred.expires_at and cred.expires_at < datetime.now(timezone.utc):
            return None, "Credential expired"

        # Verify secret (constant-time comparison to prevent timing attacks)
        import hmac
        secret_hash = sm3_hash(app_secret)
        if not hmac.compare_digest(secret_hash, cred.app_secret_hash):
            return None, "Invalid app_secret"

        # IP whitelist check
        if cred.allowed_ips and client_ip:
            if client_ip not in cred.allowed_ips:
                return None, f"IP {client_ip} not in whitelist"

        # Update last_used_at
        cred.last_used_at = datetime.now(timezone.utc)
        await db.flush()

        return cred, None

    # === Authorization ===

    async def authorize(self, db: AsyncSession, cred: AppCredential,
                        operation: str, target_product_id: str | None = None) -> tuple[Contract | None, str | None]:
        """Authorize an operation against the contract.

        Returns (contract, error_message).
        """
        result = await db.execute(
            select(Contract).where(Contract.id == cred.contract_id)
        )
        contract = result.scalar_one_or_none()

        if not contract:
            return None, "Contract not found"

        if contract.status not in (ContractStatus.ACTIVE.value, ContractStatus.SIGNED.value):
            return None, f"Contract is {contract.status}"

        # Check contract expiry
        if hasattr(contract, 'expires_at') and contract.expires_at:
            if contract.expires_at < datetime.now(timezone.utc):
                return None, "Contract expired"

        # Check operation is allowed
        if contract.allowed_operations:
            allowed_ops = [op.strip() for op in contract.allowed_operations.split(",")]
            if operation not in allowed_ops:
                return None, f"Operation '{operation}' not allowed by contract"

        # Check target product is in contract scope
        if target_product_id:
            product_ids = [str(pid) for pid in contract.product_ids]
            if str(target_product_id) not in product_ids:
                return None, "Data product not in contract scope"

        return contract, None

    # === Metering ===

    async def check_quota(self, db: AsyncSession, cred: AppCredential,
                          rows_requested: int = 0) -> tuple[bool, str]:
        """Check if the request is within daily quota limits (Redis-backed).

        Returns (allowed, reason).
        """
        usage = await quota_manager.get_gateway_usage(cred.app_id)

        # Pre-check: already at or over limit?
        if cred.quota_rows > 0 and usage["rows"] >= cred.quota_rows:
            return False, f"daily row quota exhausted ({usage['rows']}/{cred.quota_rows})"
        if cred.quota_bytes > 0 and usage["bytes"] >= cred.quota_bytes:
            return False, f"daily byte quota exhausted ({usage['bytes']}/{cred.quota_bytes})"

        return True, "within quota"

    async def increment_quota(self, cred: AppCredential,
                              rows: int = 0, bytes_count: int = 0) -> tuple[bool, str]:
        """Increment gateway quota counters after execution.

        Returns (allowed, reason). If exceeded, the counter still increments
        (preventing further requests) but the caller should flag the overage.
        """
        return await quota_manager.check_gateway_quota(
            app_id=cred.app_id,
            rows_increment=rows,
            bytes_increment=bytes_count,
            rows_limit=cred.quota_rows,
            bytes_limit=cred.quota_bytes,
        )

    def record_metering(self, record: MeteringRecord):
        """Record metering data to ClickHouse."""
        try:
            client = self._get_clickhouse()
            if not client:
                return
            client.execute(
                """INSERT INTO gateway_metering
                   (request_id, timestamp, contract_id, app_id, consumer_id,
                    product_id, operation, rows_returned, bytes_returned,
                    duration_ms, status_code)
                   VALUES""",
                [[
                    record.request_id,
                    record.timestamp,
                    record.contract_id,
                    record.app_id,
                    record.consumer_id,
                    record.product_id,
                    record.operation,
                    record.rows_returned,
                    record.bytes_returned,
                    record.duration_ms,
                    record.status_code,
                ]],
            )
        except Exception as e:
            logger.warning(f"[gateway] Metering write failed: {e}")

    # === Data Product Access ===

    async def execute_query(self, db: AsyncSession, contract: Contract,
                            product_id: str, sql: str, fmt: str = "json") -> GatewayResponse:
        """Execute a SQL query against a structured data product."""
        # Get data product
        result = await db.execute(
            select(DataProduct).where(DataProduct.id == uuid.UUID(product_id))
        )
        product = result.scalar_one_or_none()
        if not product:
            return GatewayResponse(success=False, error="Data product not found", status_code=404)

        # Route based on product type
        if product.product_type in ("structured", "semi-structured"):
            return await self._query_duckdb(product, sql, fmt)
        elif product.product_type == "unstructured":
            return GatewayResponse(
                success=False,
                error="Use /access endpoint for unstructured data products",
                status_code=400,
            )
        else:
            return GatewayResponse(
                success=False,
                error=f"Unsupported product type: {product.product_type}",
                status_code=400,
            )

    async def _query_duckdb(self, product: DataProduct, sql: str, fmt: str) -> GatewayResponse:
        """Execute query via DuckDB engine."""
        from app.services.secure_duckdb import SecureDuckDBEngine

        engine = None
        try:
            engine = SecureDuckDBEngine(f"gw-{product.id}", mode="memory")

            if product.encrypted_storage_path:
                records = self._load_product_records(product)
                if not records:
                    return GatewayResponse(
                        success=False,
                        error="Data product storage is empty or unsupported",
                        status_code=400,
                    )
                for table_name in self._query_table_names(sql, product):
                    engine.register_table(table_name, records, source=str(product.id))
            else:
                return GatewayResponse(
                    success=False,
                    error="Data product has no storage path",
                    status_code=400,
                )

            result = engine.execute_query_decrypted(sql)

            if not result.success:
                return GatewayResponse(success=False, error=result.error, status_code=400)

            return GatewayResponse(
                success=True,
                data={
                    "columns": result.columns,
                    "rows": result.rows,
                    "row_count": result.row_count,
                    "duration_ms": result.duration_ms,
                    "truncated": result.truncated,
                },
                metering={
                    "rows": result.row_count,
                    "bytes": sum(len(str(r)) for r in result.rows),
                },
            )
        except Exception as e:
            return GatewayResponse(success=False, error=str(e), status_code=500)
        finally:
            if engine is not None:
                try:
                    engine.close()
                except Exception:
                    pass

    def _load_product_records(self, product: DataProduct) -> list[dict]:
        """Load structured product data from encrypted object storage."""
        from app.services.storage_service import storage_service

        raw = storage_service.download(product.encrypted_storage_path)
        text = raw.decode("utf-8-sig")
        stripped = text.strip()
        if not stripped:
            return []

        if stripped.startswith("[") or stripped.startswith("{"):
            parsed = json.loads(stripped)
            if isinstance(parsed, list):
                return [dict(item) for item in parsed if isinstance(item, dict)]
            if isinstance(parsed, dict):
                if isinstance(parsed.get("data"), list):
                    return [dict(item) for item in parsed["data"] if isinstance(item, dict)]
                return [parsed]

        # JSONL fallback before CSV because semi-structured products often use
        # one JSON object per line with no surrounding array.
        lines = [line.strip() for line in stripped.splitlines() if line.strip()]
        if lines and all(line.startswith("{") and line.endswith("}") for line in lines):
            return [json.loads(line) for line in lines]

        reader = csv.DictReader(io.StringIO(text))
        return [dict(row) for row in reader]

    def _query_table_names(self, sql: str, product: DataProduct) -> list[str]:
        """Return table aliases that should be registered for this query."""
        names = {"data", f"product_{str(product.id).replace('-', '_')[:12]}"}
        if product.name:
            safe_product_name = re.sub(r"\W+", "_", product.name.strip()).strip("_")
            if safe_product_name:
                names.add(safe_product_name)

        for match in re.finditer(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", sql, re.IGNORECASE):
            names.add(match.group(1))
        return sorted(names)

    async def get_presigned_url(self, product_id: str) -> GatewayResponse:
        """Get a presigned URL for unstructured data product access."""
        # In production, this would generate a MinIO presigned URL
        return GatewayResponse(
            success=True,
            data={"url": f"/api/v1/data-products/{product_id}/download", "expires_in": 3600},
        )

    # === Content Security ===

    def apply_content_security(self, data: dict, contract: Contract) -> tuple[dict, dict]:
        """Apply content security measures to query results.

        Returns (secured_data, security_report).
        """
        security_report = {
            "pii_detected": False,
            "pii_redacted": False,
            "watermark_applied": False,
            "rows_filtered": 0,
        }

        if not isinstance(data, dict) or "rows" not in data:
            return data, security_report

        rows = data.get("rows", [])
        columns = data.get("columns", [])

        from app.services.output_security import (
            inspect_text_output,
            inspection_to_report,
            redact_data,
            safe_json_dumps,
            should_block,
        )

        inspection = inspect_text_output(
            safe_json_dumps({"columns": columns, "rows": rows}),
            user_id=str(getattr(contract, "buyer_id", "")),
            session_id=str(getattr(contract, "id", "")),
            sandbox_mode="query",
        )
        inspection_report = inspection_to_report(inspection)
        security_report.update({
            "inspection_passed": inspection.passed,
            "inspection_blocked": should_block(inspection),
            "findings_count": len(inspection.findings),
            "stage_results": inspection.stage_results,
            "signature": inspection.signature,
        })

        if inspection.findings:
            security_report["pii_detected"] = True
            data["rows"] = redact_data(rows)
            security_report["pii_redacted"] = True
        else:
            data["rows"] = rows

        data["_watermark"] = inspection.watermark
        data["_signature"] = inspection.signature
        security_report["watermark_applied"] = bool(inspection.watermark)
        security_report["inspection"] = inspection_report

        return data, security_report

    def _redact_pii(self, text: str) -> tuple[str, bool]:
        """Detect and redact PII from text. Returns (redacted_text, had_pii)."""
        import re
        detected = False

        # Chinese phone number
        if re.search(r'1[3-9]\d{9}', text):
            text = re.sub(r'1[3-9]\d{9}', '1**********', text)
            detected = True

        # Email
        if re.search(r'[\w.-]+@[\w.-]+\.\w+', text):
            text = re.sub(r'([\w.-]+)@([\w.-]+\.\w+)', lambda m: m.group(1)[:2] + '***@' + m.group(2), text)
            detected = True

        # Chinese ID card
        if re.search(r'\d{17}[\dXx]', text):
            text = re.sub(r'\d{17}[\dXx]', '*******************', text)
            detected = True

        return text, detected

    # === ClickHouse ===

    def _get_clickhouse(self):
        if self._clickhouse_client is None:
            try:
                from clickhouse_driver import Client
                from app.core.config import get_settings
                settings = get_settings()
                url = settings.CLICKHOUSE_URL
                host, port, user, password, database = "localhost", 9000, "default", "", "cds_audit"
                if not password:
                    logger.warning("[SECURITY] ClickHouse connecting with empty password")
                if "://" in url:
                    from urllib.parse import urlparse
                    parsed = urlparse(url)
                    host = parsed.hostname or "localhost"
                    port = parsed.port or 9000
                    user = parsed.username or "default"
                    password = parsed.password or ""
                    database = parsed.path.lstrip("/") or "cds_audit"
                elif ":" in url:
                    h, p = url.rsplit(":", 1)
                    host, port = h, int(p)
                else:
                    host = url
                self._clickhouse_client = Client(
                    host=host, port=port, database=database,
                    user=user, password=password,
                )
            except Exception:
                self._clickhouse_client = None
        return self._clickhouse_client


# Singleton
gateway_service = GatewayService()
