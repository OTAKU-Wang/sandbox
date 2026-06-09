"""Audit logging service — writes to PostgreSQL + optional ClickHouse."""
import logging
import uuid
import json
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog

logger = logging.getLogger(__name__)


class AuditService:
    """Audit trail service for compliance and provenance.

    Each audit event is SM2-signed for non-repudiation per SS-06 spec.
    """

    _signing_keypair = None  # Lazy-initialized SM2 keypair
    _hsm_key_id = None  # HSM key identifier for signing

    def __init__(self):
        self._clickhouse_client = None

    def _get_clickhouse(self):
        if self._clickhouse_client is None:
            try:
                from clickhouse_driver import Client
                from app.core.config import get_settings
                settings = get_settings()
                # Parse CLICKHOUSE_URL: "host:port" or "clickhouse://user:pass@host:port/db"
                url = settings.CLICKHOUSE_URL
                host = "localhost"
                port = 9000
                user = "default"
                password = ""
                database = "cds_audit"
                if "://" in url:
                    # Parse: clickhouse://user:pass@host:port/db
                    from urllib.parse import urlparse
                    parsed = urlparse(url)
                    host = parsed.hostname or "localhost"
                    port = parsed.port or 9000
                    user = parsed.username or "default"
                    password = parsed.password or ""
                    database = parsed.path.lstrip("/") or "cds_audit"
                elif ":" in url:
                    host, port = url.rsplit(":", 1)
                    port = int(port)
                else:
                    host = url
                self._clickhouse_client = Client(
                    host=host, port=port, database=database,
                    user=user, password=password,
                )
            except Exception:
                self._clickhouse_client = None
        return self._clickhouse_client

    async def log(
        self,
        db: AsyncSession,
        action: str,
        resource_type: str,
        user_id: uuid.UUID | None = None,
        session_id: uuid.UUID | None = None,
        resource_id: str | None = None,
        detail: dict | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuditLog:
        """Create an audit log entry in PostgreSQL."""
        # SM2 sign the audit event for non-repudiation (P0-2: HSM-first)
        signature = None
        try:
            from app.services.crypto_service import crypto_service
            event_data = f"{action}|{resource_type}|{resource_id or ''}|{user_id or ''}"
            if AuditService._hsm_key_id is None:
                try:
                    _, key_id = crypto_service.generate_hsm_signing_keypair("audit-signing")
                    AuditService._hsm_key_id = key_id
                except Exception:
                    # HSM unavailable — fall back to software keypair
                    if AuditService._signing_keypair is None:
                        AuditService._signing_keypair = crypto_service.generate_keypair()
                    AuditService._hsm_key_id = "__software__"

            if AuditService._hsm_key_id == "__software__":
                kp = AuditService._signing_keypair
                sm2_sig = crypto_service.sign(event_data.encode(), kp.private_key, kp.public_key)
                signature = sm2_sig.signature
            else:
                sm2_sig = crypto_service.sign_with_hsm(event_data.encode(), AuditService._hsm_key_id)
                signature = sm2_sig.signature
        except Exception as e:
            logger.error(f"[CRITICAL] Audit SM2 signing failed — non-repudiation compromised: {e}")
            # Record signing failure in detail for forensic review
            if detail is None:
                detail = {}
            detail["_sm2_sign_error"] = str(e)

        entry = AuditLog(
            user_id=user_id,
            session_id=session_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            detail=detail,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        # Store signature in detail if available
        if signature:
            if entry.detail is None:
                entry.detail = {}
            entry.detail["_sm2_signature"] = signature

        db.add(entry)
        await db.flush()

        # Also write to ClickHouse if available
        self._write_to_clickhouse(entry)

        return entry

    def _write_to_clickhouse(self, entry: AuditLog):
        """Write audit log to ClickHouse with expanded 30+ column schema."""
        ch = self._get_clickhouse()
        if ch:
            try:
                detail = entry.detail or {}
                ch.execute(
                    """INSERT INTO audit_events (
                        event_id, timestamp, user_id, session_id,
                        user_role, user_region,
                        action, action_category, resource_type, resource_id, resource_name,
                        ip_address, user_agent, request_method, request_path, request_id,
                        sm2_signature, integrity_hash, merkle_root, blockchain_tx_hash,
                        data_product_id, data_product_version, data_product_type,
                        sandbox_id, sandbox_type, compute_duration_ms,
                        rows_affected, bytes_read, bytes_written,
                        contract_id, policy_decision, policy_reason,
                        security_level, encryption_key_id, column_encrypted_fields,
                        training_job_id, model_id, dp_epsilon, dp_delta,
                        error_code, error_message,
                        detail, tags
                    ) VALUES""",
                    [(
                        str(entry.id),
                        entry.created_at or datetime.now(timezone.utc),
                        str(entry.user_id) if entry.user_id else "00000000-0000-0000-0000-000000000000",
                        str(entry.session_id) if entry.session_id else "00000000-0000-0000-0000-000000000000",
                        detail.get("user_role", ""),
                        detail.get("user_region", ""),
                        entry.action,
                        detail.get("action_category", ""),
                        entry.resource_type,
                        entry.resource_id or "",
                        detail.get("resource_name", ""),
                        entry.ip_address or "0.0.0.0",
                        entry.user_agent or "",
                        detail.get("request_method", ""),
                        detail.get("request_path", ""),
                        detail.get("request_id", str(uuid.uuid4())),
                        detail.get("_sm2_signature", ""),
                        detail.get("integrity_hash", ""),
                        detail.get("merkle_root", ""),
                        detail.get("blockchain_tx_hash", ""),
                        detail.get("data_product_id", "00000000-0000-0000-0000-000000000000"),
                        detail.get("data_product_version", 0),
                        detail.get("data_product_type", ""),
                        detail.get("sandbox_id", "00000000-0000-0000-0000-000000000000"),
                        detail.get("sandbox_type", ""),
                        detail.get("compute_duration_ms", 0),
                        detail.get("rows_affected", 0),
                        detail.get("bytes_read", 0),
                        detail.get("bytes_written", 0),
                        detail.get("contract_id", "00000000-0000-0000-0000-000000000000"),
                        detail.get("policy_decision", ""),
                        detail.get("policy_reason", ""),
                        detail.get("security_level", ""),
                        detail.get("encryption_key_id", ""),
                        detail.get("column_encrypted_fields", []),
                        detail.get("training_job_id", "00000000-0000-0000-0000-000000000000"),
                        detail.get("model_id", ""),
                        detail.get("dp_epsilon", 0.0),
                        detail.get("dp_delta", 0.0),
                        detail.get("error_code", ""),
                        detail.get("error_message", ""),
                        json.dumps(detail),
                        detail.get("tags", []),
                    )],
                )
            except Exception as e:
                logger.debug(f"ClickHouse audit write failed (non-fatal): {e}")

    async def log_to_blockchain(self, merkle_root: str) -> str | None:
        """Submit Merkle root to blockchain for immutable attestation.

        Uses the configured blockchain adapter (FISCO BCOS, AntChain, or PG append-only).
        Returns tx_hash or None if blockchain is unavailable.
        """
        from app.services.blockchain_adapter import blockchain_adapter
        try:
            result = await blockchain_adapter.anchor(
                merkle_root.encode(),
                metadata={"type": "audit_merkle_root", "root": merkle_root},
            )
            if result.success and result.anchor:
                return result.anchor.tx_hash
        except Exception as e:
            logger.warning(f"Blockchain anchoring failed (non-fatal): {e}")
        return None


audit_service = AuditService()
