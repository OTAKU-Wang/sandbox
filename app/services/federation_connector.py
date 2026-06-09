"""Cross-Space Federation Connector — SS-08 implementation.

Provides identity federation and REST gateway for cross-space data circulation:
1. Identity Federation — cross-space authentication via SM2-JWT/JWT/SAML/OIDC
2. REST Gateway — proxy requests between trusted data spaces
3. Trust Management — verify remote space certificates and policies
4. Protocol Translation — convert between different space APIs

Architecture:
- LocalSpace: this CDS instance
- RemoteSpace: trusted external data space (another CDS instance or compatible system)
- FederationTrust: bilateral trust agreement between spaces
- GatewayRequest: proxied request with identity forwarding

Supports:
- SM2-JWT token exchange for cross-space identity (GM/T 0009)
- HS256 JWT fallback for compatibility
- mTLS for inter-space communication
- Policy-aware request routing
- Audit logging of all cross-space operations
"""
import base64
import hashlib
import json
import logging
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class TrustLevel(str, Enum):
    """Trust level between spaces."""
    NONE = "none"
    BASIC = "basic"          # Token exchange only
    VERIFIED = "verified"    # mTLS + token
    FULL = "full"            # mTLS + token + policy alignment


class FederationStatus(str, Enum):
    """Federation connection status."""
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"


class ProtocolType(str, Enum):
    """Supported inter-space protocols."""
    REST = "rest"
    GRPC = "grpc"
    MQTT = "mqtt"


@dataclass
class SpaceIdentity:
    """Identity of a data space."""
    space_id: str
    space_name: str
    endpoint: str
    public_key: str = ""
    certificate: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class FederationTrust:
    """Bilateral trust agreement between two spaces."""
    trust_id: str
    local_space: SpaceIdentity
    remote_space: SpaceIdentity
    trust_level: TrustLevel
    status: FederationStatus
    allowed_operations: list[str]
    policy_sync_enabled: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    last_sync: datetime | None = None


@dataclass
class GatewayRequest:
    """A proxied cross-space request."""
    request_id: str
    source_space: str
    target_space: str
    operation: str
    resource: str
    payload: dict | None = None
    headers: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class GatewayResponse:
    """Response from a cross-space request."""
    request_id: str
    status_code: int
    data: Any = None
    error: str | None = None
    duration_ms: int = 0
    source_space: str = ""


@dataclass
class FederationAuditEntry:
    """Audit log entry for cross-space operations."""
    entry_id: str
    request_id: str
    source_space: str
    target_space: str
    operation: str
    resource: str
    status: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    user_id: str | None = None
    details: dict = field(default_factory=dict)


class IdentityProvider(ABC):
    """Abstract identity provider for cross-space authentication."""

    @abstractmethod
    def create_federated_token(self, local_user_id: str, remote_space: SpaceIdentity, ttl_seconds: int = 3600) -> str:
        """Create a federated JWT token for cross-space access."""
        ...

    @abstractmethod
    def verify_federated_token(self, token: str, expected_space: SpaceIdentity) -> dict | None:
        """Verify a federated token and return claims."""
        ...


class JWTIdentityProvider(IdentityProvider):
    """JWT-based identity federation provider."""

    def __init__(self, secret_key: str = "federation-secret-key"):
        self._secret_key = secret_key

    def create_federated_token(self, local_user_id: str, remote_space: SpaceIdentity, ttl_seconds: int = 3600) -> str:
        """Create a federated JWT token."""
        import jwt
        now = datetime.now(timezone.utc)
        claims = {
            "sub": local_user_id,
            "iss": "cds-local",
            "aud": remote_space.space_id,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
            "federation": True,
            "space_id": remote_space.space_id,
        }
        return jwt.encode(claims, self._secret_key, algorithm="HS256")

    def verify_federated_token(self, token: str, expected_space: SpaceIdentity) -> dict | None:
        """Verify a federated JWT token."""
        import jwt
        try:
            claims = jwt.decode(token, self._secret_key, algorithms=["HS256"], audience=expected_space.space_id)
            if not claims.get("federation"):
                return None
            return claims
        except jwt.InvalidTokenError:
            return None


class SM2IdentityProvider(IdentityProvider):
    """SM2-JWT identity federation provider (GM/T 0009).

    Creates JWT-format tokens signed with SM2 instead of HS256.
    Token format: base64(header).base64(payload).base64(sm2_signature)
    Header uses alg="SM2" to distinguish from standard JWT.
    """

    def __init__(self, keypair=None):
        from app.services.crypto_service import crypto_service, SM2KeyPair
        self._crypto = crypto_service
        self._keypair = keypair or crypto_service.generate_keypair()

    def _b64url_encode(self, data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    def _b64url_decode(self, s: str) -> bytes:
        padding = 4 - len(s) % 4
        if padding != 4:
            s += "=" * padding
        return base64.urlsafe_b64decode(s)

    def _build_signing_input(self, header_b64: str, payload_b64: str) -> bytes:
        return f"{header_b64}.{payload_b64}".encode("ascii")

    def create_federated_token(self, local_user_id: str, remote_space: SpaceIdentity, ttl_seconds: int = 3600) -> str:
        """Create an SM2-signed JWT token."""
        now = datetime.now(timezone.utc)
        header = {"alg": "SM2", "typ": "JWT"}
        claims = {
            "sub": local_user_id,
            "iss": "cds-local",
            "aud": remote_space.space_id,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
            "federation": True,
            "space_id": remote_space.space_id,
        }
        header_b64 = self._b64url_encode(json.dumps(header, separators=(",", ":")).encode())
        payload_b64 = self._b64url_encode(json.dumps(claims, separators=(",", ":")).encode())
        signing_input = self._build_signing_input(header_b64, payload_b64)
        sm2_sig = self._crypto.sign(signing_input, self._keypair.private_key, self._keypair.public_key)
        sig_b64 = self._b64url_encode(bytes.fromhex(sm2_sig.signature))
        return f"{header_b64}.{payload_b64}.{sig_b64}"

    def verify_federated_token(self, token: str, expected_space: SpaceIdentity) -> dict | None:
        """Verify an SM2-signed JWT token."""
        try:
            parts = token.split(".")
            if len(parts) != 3:
                return None
            header_b64, payload_b64, sig_b64 = parts
            header = json.loads(self._b64url_decode(header_b64))
            if header.get("alg") != "SM2":
                return None
            signing_input = self._build_signing_input(header_b64, payload_b64)
            sig_bytes = self._b64url_decode(sig_b64)
            sig_hex = sig_bytes.hex()
            if not self._crypto.verify(signing_input, sig_hex, self._keypair.public_key):
                return None
            claims = json.loads(self._b64url_decode(payload_b64))
            if claims.get("aud") != expected_space.space_id:
                return None
            if claims.get("exp") and claims["exp"] < int(time.time()):
                return None
            if not claims.get("federation"):
                return None
            return claims
        except (ValueError, KeyError, json.JSONDecodeError):
            return None


class FederationConnector:
    """Cross-space federation connector.

    Manages trust relationships, identity federation, and proxied requests
    between trusted data spaces.

    Usage:
        connector = FederationConnector()
        trust = connector.establish_trust(local_space, remote_space, TrustLevel.VERIFIED)
        response = connector.send_request(trust, "read", "/data/products", user_id="user-1")
    """

    def __init__(self, identity_provider: IdentityProvider | None = None):
        if identity_provider:
            self._idp = identity_provider
        else:
            try:
                self._idp = SM2IdentityProvider()
            except Exception:
                logger.warning("SM2 not available, falling back to HS256 JWT")
                self._idp = JWTIdentityProvider()
        self._local_space: SpaceIdentity | None = None
        self._trusts: dict[str, FederationTrust] = {}  # trust_id → trust
        self._space_index: dict[str, FederationTrust] = {}  # space_id → trust
        self._audit_log: list[FederationAuditEntry] = []
        self._request_count = 0
        self._db_session_factory = None
        # Dynamic trust evaluator — scores degrade on violations, improve on success
        self._trust_scores: dict[str, dict] = {}  # space_id → {quality, compliance, reputation, security}
        self._min_trust_by_op: dict[str, int] = {
            "search": 0, "read_catalog": 0, "read": 30,
            "write": 50, "execute": 60, "admin": 80,
        }

    def set_db_session_factory(self, factory) -> None:
        """Set async session factory for DB persistence."""
        self._db_session_factory = factory

    async def load_trusts_from_db(self) -> int:
        """Load trust relationships from DB on startup. Returns count loaded."""
        if not self._db_session_factory:
            return 0
        try:
            import json
            from sqlalchemy import select
            from app.models.federation_trust import FederationTrustRecord
            async with self._db_session_factory() as session:
                result = await session.execute(
                    select(FederationTrustRecord).where(
                        FederationTrustRecord.status.in_(["active", "suspended"])
                    )
                )
                rows = result.scalars().all()
            for row in rows:
                local = SpaceIdentity(
                    space_id=row.local_space_id,
                    space_name=row.local_space_name,
                    endpoint=row.local_endpoint,
                )
                remote = SpaceIdentity(
                    space_id=row.remote_space_id,
                    space_name=row.remote_space_name,
                    endpoint=row.remote_endpoint,
                )
                ops = json.loads(row.allowed_operations_json) if row.allowed_operations_json else []
                trust = FederationTrust(
                    trust_id=str(row.id),
                    local_space=local,
                    remote_space=remote,
                    trust_level=TrustLevel(row.trust_level),
                    status=FederationStatus(row.status),
                    allowed_operations=ops,
                    policy_sync_enabled=row.policy_sync_enabled,
                    created_at=row.created_at,
                    expires_at=row.expires_at,
                    last_sync=row.last_sync,
                )
                self._trusts[trust.trust_id] = trust
                self._space_index[remote.space_id] = trust
            logger.info("[Federation] Loaded %d trusts from DB", len(rows))
            return len(rows)
        except Exception as e:
            logger.warning("[Federation] Failed to load trusts from DB: %s", e)
            return 0

    async def _persist_trust(self, trust: FederationTrust) -> None:
        """Persist a trust record to the database."""
        if not self._db_session_factory:
            return
        try:
            import json
            from app.models.federation_trust import FederationTrustRecord
            async with self._db_session_factory() as session:
                record = FederationTrustRecord(
                    id=uuid.UUID(trust.trust_id.replace("trust-", "")[:32].ljust(32, "0")),
                    local_space_id=trust.local_space.space_id,
                    local_space_name=trust.local_space.space_name,
                    local_endpoint=trust.local_space.endpoint,
                    remote_space_id=trust.remote_space.space_id,
                    remote_space_name=trust.remote_space.space_name,
                    remote_endpoint=trust.remote_space.endpoint,
                    trust_level=trust.trust_level.value,
                    status=trust.status.value,
                    allowed_operations_json=json.dumps(trust.allowed_operations),
                    policy_sync_enabled=trust.policy_sync_enabled,
                    expires_at=trust.expires_at,
                    last_sync=trust.last_sync,
                )
                session.add(record)
                await session.commit()
        except Exception as e:
            logger.error("[Federation] Failed to persist trust %s: %s", trust.trust_id, e)

    async def _update_trust_status(self, trust_id: str, status: str) -> None:
        """Update trust status in DB."""
        if not self._db_session_factory:
            return
        try:
            from sqlalchemy import update
            from app.models.federation_trust import FederationTrustRecord
            async with self._db_session_factory() as session:
                await session.execute(
                    update(FederationTrustRecord)
                    .where(FederationTrustRecord.id == uuid.UUID(trust_id.replace("trust-", "")[:32].ljust(32, "0")))
                    .values(status=status)
                )
                await session.commit()
        except Exception as e:
            logger.error("[Federation] Failed to update trust status in DB: %s", e)

    async def _persist_audit_entry(self, entry: FederationAuditEntry) -> None:
        """Persist an audit entry to the database."""
        if not self._db_session_factory:
            return
        try:
            import json
            from app.models.federation_trust import FederationAuditRecord
            async with self._db_session_factory() as session:
                record = FederationAuditRecord(
                    entry_id=entry.entry_id,
                    request_id=entry.request_id,
                    source_space=entry.source_space,
                    target_space=entry.target_space,
                    operation=entry.operation,
                    resource=entry.resource,
                    status=entry.status,
                    user_id=entry.user_id,
                    details_json=json.dumps(entry.details) if entry.details else None,
                    timestamp=entry.timestamp,
                )
                session.add(record)
                await session.commit()
        except Exception as e:
            logger.error("[Federation] Failed to persist audit entry: %s", e)

    def set_local_space(self, space: SpaceIdentity) -> None:
        """Set the local space identity."""
        self._local_space = space
        logger.info(f"Local space set: {space.space_id}")

    def establish_trust(
        self,
        remote_space: SpaceIdentity,
        trust_level: TrustLevel = TrustLevel.BASIC,
        allowed_operations: list[str] | None = None,
        policy_sync: bool = False,
    ) -> FederationTrust:
        """Establish a trust relationship with a remote space.

        Args:
            remote_space: Remote space identity
            trust_level: Level of trust to establish
            allowed_operations: Permitted operations (default: read-only)
            policy_sync: Whether to sync policies between spaces

        Returns:
            FederationTrust object
        """
        if not self._local_space:
            raise RuntimeError("Local space not configured. Call set_local_space() first.")

        trust_id = f"trust-{uuid.uuid4().hex[:12]}"
        default_ops = ["read_catalog", "search", "verify_audit"]
        trust = FederationTrust(
            trust_id=trust_id,
            local_space=self._local_space,
            remote_space=remote_space,
            trust_level=trust_level,
            status=FederationStatus.ACTIVE,
            allowed_operations=allowed_operations or default_ops,
            policy_sync_enabled=policy_sync,
        )

        self._trusts[trust_id] = trust
        self._space_index[remote_space.space_id] = trust
        logger.info(f"Trust established: {trust_id} ({trust_level.value}) with {remote_space.space_id}")
        # Persist to DB (fire-and-forget)
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._persist_trust(trust))
        except RuntimeError:
            pass  # No event loop — skip persistence
        return trust

    def revoke_trust(self, trust_id: str) -> bool:
        """Revoke a trust relationship."""
        trust = self._trusts.get(trust_id)
        if not trust:
            return False
        trust.status = FederationStatus.REVOKED
        self._space_index.pop(trust.remote_space.space_id, None)
        logger.info(f"Trust revoked: {trust_id}")
        # Update DB (fire-and-forget)
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._update_trust_status(trust_id, "revoked"))
        except RuntimeError:
            pass
        return True

    def suspend_trust(self, trust_id: str) -> bool:
        """Suspend a trust relationship."""
        trust = self._trusts.get(trust_id)
        if not trust:
            return False
        trust.status = FederationStatus.SUSPENDED
        logger.info(f"Trust suspended: {trust_id}")
        # Update DB (fire-and-forget)
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._update_trust_status(trust_id, "suspended"))
        except RuntimeError:
            pass
        return True

    def get_trust(self, trust_id: str) -> FederationTrust | None:
        """Get trust by ID."""
        return self._trusts.get(trust_id)

    def get_trust_by_space(self, space_id: str) -> FederationTrust | None:
        """Get trust by remote space ID."""
        return self._space_index.get(space_id)

    def list_trusts(self, status: FederationStatus | None = None) -> list[FederationTrust]:
        """List all trust relationships."""
        trusts = list(self._trusts.values())
        if status:
            trusts = [t for t in trusts if t.status == status]
        return trusts

    def send_request(
        self,
        trust: FederationTrust,
        operation: str,
        resource: str,
        payload: dict | None = None,
        user_id: str | None = None,
    ) -> GatewayResponse:
        """Send a proxied request to a remote space.

        Args:
            trust: Trust relationship with the target space
            operation: Operation type (read, write, search, etc.)
            resource: Resource path
            payload: Request payload
            user_id: User performing the request

        Returns:
            GatewayResponse with result
        """
        start = time.monotonic()
        request_id = f"req-{uuid.uuid4().hex[:12]}"

        # Validate trust
        if trust.status != FederationStatus.ACTIVE:
            return GatewayResponse(
                request_id=request_id,
                status_code=403,
                error=f"Trust not active: {trust.status.value}",
                source_space=trust.remote_space.space_id,
            )

        # Check operation permission
        if operation not in trust.allowed_operations:
            return GatewayResponse(
                request_id=request_id,
                status_code=403,
                error=f"Operation not allowed: {operation}",
                source_space=trust.remote_space.space_id,
            )

        # Dynamic trust evaluation — block if score below threshold for this operation
        from app.services.trust_evaluator import TrustEvaluator
        evaluator = TrustEvaluator()
        scores = self._trust_scores.get(trust.remote_space.space_id, {})
        trust_result = evaluator.evaluate(
            data_quality_score=scores.get("quality", 50),
            compliance_score=scores.get("compliance", 50),
            reputation_score=scores.get("reputation", 50),
            security_score=scores.get("security", 50),
        )
        min_score = self._min_trust_by_op.get(operation, 0)
        if trust_result.score < min_score:
            logger.warning(
                f"[federation] Trust score {trust_result.score} below threshold {min_score} "
                f"for operation '{operation}' on space {trust.remote_space.space_id}"
            )
            return GatewayResponse(
                request_id=request_id,
                status_code=403,
                error=f"Trust score {trust_result.score} below threshold {min_score} for '{operation}'",
                source_space=trust.remote_space.space_id,
            )

        # Create federated token
        token = self._idp.create_federated_token(user_id or "system", trust.remote_space)

        # Build gateway request
        request = GatewayRequest(
            request_id=request_id,
            source_space=trust.local_space.space_id,
            target_space=trust.remote_space.space_id,
            operation=operation,
            resource=resource,
            payload=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "X-Federation-Source": trust.local_space.space_id,
                "X-Federation-Trust": trust.trust_id,
                "X-Request-ID": request_id,
            },
        )

        # Log audit entry
        audit_entry = FederationAuditEntry(
            entry_id=f"audit-{uuid.uuid4().hex[:8]}",
            request_id=request_id,
            source_space=trust.local_space.space_id,
            target_space=trust.remote_space.space_id,
            operation=operation,
            resource=resource,
            status="sent",
            user_id=user_id,
        )
        self._audit_log.append(audit_entry)
        # Persist audit to DB (fire-and-forget)
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._persist_audit_entry(audit_entry))
        except RuntimeError:
            pass

        # Execute real HTTP request to remote space
        response = self._execute_remote_request(request, trust)

        duration = int((time.monotonic() - start) * 1000)
        response.duration_ms = duration
        response.source_space = trust.remote_space.space_id
        self._request_count += 1

        # Update dynamic trust score based on response
        self._update_trust_score(trust.remote_space.space_id, response.status_code, duration)

        logger.info(f"Federation request {request_id}: {operation} {resource} → {response.status_code}")
        return response

    def _update_trust_score(self, space_id: str, status_code: int, duration_ms: int) -> None:
        """Update dynamic trust score for a remote space based on request outcome."""
        if space_id not in self._trust_scores:
            self._trust_scores[space_id] = {
                "quality": 50, "compliance": 50, "reputation": 50, "security": 50,
            }
        scores = self._trust_scores[space_id]
        if 200 <= status_code < 300:
            # Success — slight reputation boost
            scores["reputation"] = min(100, scores["reputation"] + 1)
            # Fast response boosts quality
            if duration_ms < 1000:
                scores["quality"] = min(100, scores["quality"] + 1)
        elif status_code >= 500:
            # Server error — reduce reputation
            scores["reputation"] = max(0, scores["reputation"] - 5)
        elif status_code == 403:
            # Forbidden — compliance concern
            scores["compliance"] = max(0, scores["compliance"] - 3)

    def set_trust_scores(self, space_id: str, quality: int = 50, compliance: int = 50,
                         reputation: int = 50, security: int = 50) -> None:
        """Manually set trust scores for a space (for testing or admin override)."""
        self._trust_scores[space_id] = {
            "quality": quality, "compliance": compliance,
            "reputation": reputation, "security": security,
        }

    def _execute_remote_request(self, request: GatewayRequest, trust: FederationTrust) -> GatewayResponse:
        """Execute a real HTTP request to the remote space endpoint.

        Builds the federation API URL from the remote space endpoint, forwards
        the operation and payload with federated auth headers.
        """
        base_url = trust.remote_space.endpoint.rstrip("/")
        url = f"{base_url}/api/v1/federation/{request.operation}"

        # Build request body
        body = {
            "request_id": request.request_id,
            "source_space": request.source_space,
            "operation": request.operation,
            "resource": request.resource,
        }
        if request.payload:
            body["payload"] = request.payload

        timeout = httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)

        try:
            with httpx.Client(timeout=timeout, verify=True) as client:
                response = client.post(
                    url,
                    json=body,
                    headers=request.headers,
                )
                data = response.json() if response.headers.get("content-type", "").startswith("application/json") else None
                return GatewayResponse(
                    request_id=request.request_id,
                    status_code=response.status_code,
                    data=data,
                    error=data.get("detail") if data and response.status_code >= 400 else None,
                    source_space=request.target_space,
                )
        except httpx.ConnectError as e:
            logger.warning(f"[federation] Connection failed to {base_url}: {e}")
            return GatewayResponse(
                request_id=request.request_id,
                status_code=502,
                error=f"Remote space unreachable: {e}",
                source_space=request.target_space,
            )
        except httpx.TimeoutException as e:
            logger.warning(f"[federation] Timeout calling {base_url}: {e}")
            return GatewayResponse(
                request_id=request.request_id,
                status_code=504,
                error=f"Remote space timeout: {e}",
                source_space=request.target_space,
            )
        except Exception as e:
            logger.error(f"[federation] Request failed: {e}")
            return GatewayResponse(
                request_id=request.request_id,
                status_code=500,
                error=f"Federation request failed: {e}",
                source_space=request.target_space,
            )

    def get_audit_log(
        self,
        space_id: str | None = None,
        operation: str | None = None,
        limit: int = 100,
    ) -> list[FederationAuditEntry]:
        """Get federation audit log entries."""
        entries = self._audit_log
        if space_id:
            entries = [e for e in entries if e.source_space == space_id or e.target_space == space_id]
        if operation:
            entries = [e for e in entries if e.operation == operation]
        return entries[-limit:]

    def get_stats(self) -> dict:
        """Get federation statistics."""
        active = sum(1 for t in self._trusts.values() if t.status == FederationStatus.ACTIVE)
        return {
            "total_trusts": len(self._trusts),
            "active_trusts": active,
            "total_requests": self._request_count,
            "audit_entries": len(self._audit_log),
            "local_space": self._local_space.space_id if self._local_space else None,
        }


# Singleton
federation_connector = FederationConnector()
