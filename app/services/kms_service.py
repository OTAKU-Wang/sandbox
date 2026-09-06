"""KMS service — key management with HSM envelope encryption.

DEKs are wrapped with a KEK (stored in HSM) before being held in memory.
The HSMAdapter (Vault Transit or software fallback) handles all KEK operations.
Session keys are never written to disk — only distributed via env var.

P0-8: DEKs are additionally SM2-encrypted with the provider's certificate public key.
This adds a second layer of protection — even if the KEK is compromised,
the attacker still needs the SM2 private key to recover the DEK.
"""
import logging
import uuid
import os

from app.services.hsm_adapter import hsm_adapter
from app.services.crypto_service import crypto_service

logger = logging.getLogger(__name__)

# Well-known KEK ID for wrapping DEKs
_KEK_ID = "cds-master-kek"


class KMSService:
    """Key Management Service with HSM envelope encryption.

    Architecture:
      generate_data_key() → random DEK → hsm.wrap_dek(KEK, DEK) → store wrapped
                       optionally → sm2_encrypt(DEK, cert_pubkey) → store SM2 ciphertext
      get_key()           → retrieve wrapped → hsm.unwrap_dek(KEK, wrapped) → plaintext
      distribute_key()    → unwrap + return hex (with optional TEE attestation check)
      destroy_key()       → delete from store + hsm revoke

    P0-8: Two-layer encryption — KEK wrapping + SM2 encryption with cert public key.
    P0-9: TEE attestation required before key distribution.
    """

    def __init__(self):
        self._hsm = hsm_adapter
        self._wrapped_keys: dict[str, bytes] = {}  # wrapped DEK blobs
        self._sm2_encrypted_keys: dict[str, bytes] = {}  # SM2-encrypted DEK blobs
        self._cert_public_keys: dict[str, str] = {}  # key_id → SM2 public key used
        self._kek_ready = False

    def _ensure_kek(self):
        """Generate the master KEK in HSM if not already done."""
        if self._kek_ready:
            return
        try:
            self._hsm.generate_kek(_KEK_ID)
            self._kek_ready = True
            logger.info("[KMS] Master KEK initialized in HSM")
        except Exception as e:
            logger.error(f"[KMS] Failed to initialize KEK: {e}")
            raise

    def _sm2_encrypt(self, plaintext: bytes, public_key: str) -> bytes:
        """SM2-encrypt data with a certificate public key (P0-8)."""
        return crypto_service.sm2_encrypt(plaintext, public_key)

    def _sm2_decrypt(self, ciphertext: bytes, private_key: str, public_key: str) -> bytes:
        """SM2-decrypt data with a certificate private key (P0-8)."""
        return crypto_service.sm2_decrypt(ciphertext, private_key, public_key)

    def generate_data_key(self, product_id: str, cert_public_key: str | None = None) -> dict:
        """Generate a DEK and wrap it with KEK before storage.

        P0-8: If cert_public_key is provided, additionally SM2-encrypt the DEK
        with the provider's certificate public key. This creates two-layer protection:
        KEK wrapping (for HSM-based protection) + SM2 encryption (for cert-based access control).

        Returns {key_id, key_bytes, sm2_encrypted_key} — key_bytes is plaintext for immediate use only.
        """
        self._ensure_kek()
        key_id = f"dek-{product_id}-{uuid.uuid4().hex[:8]}"
        key_bytes = os.urandom(32)  # SM4-256 key

        # Layer 1: Wrap DEK with KEK — wrapped blob stored in memory
        wrapped = self._hsm.wrap_dek(_KEK_ID, key_bytes)
        self._wrapped_keys[key_id] = wrapped

        result = {"key_id": key_id, "key_bytes": key_bytes}

        # Layer 2 (P0-8): SM2-encrypt DEK with certificate public key
        if cert_public_key:
            sm2_ct = crypto_service.sm2_encrypt(key_bytes, cert_public_key)
            self._sm2_encrypted_keys[key_id] = sm2_ct
            self._cert_public_keys[key_id] = cert_public_key
            result["sm2_encrypted_key"] = sm2_ct.hex()
            logger.info(f"[KMS] Generated DEK {key_id} with SM2 encryption")
        else:
            logger.info(f"[KMS] Generated and wrapped DEK {key_id}")

        return result

    def get_key(self, key_id: str) -> bytes | None:
        """Retrieve and unwrap a key. Returns plaintext bytes or None."""
        wrapped = self._wrapped_keys.get(key_id)
        if not wrapped:
            return None
        try:
            return self._hsm.unwrap_dek(_KEK_ID, wrapped)
        except Exception as e:
            logger.error(f"[KMS] Failed to unwrap key {key_id}: {e}")
            return None

    def encrypt_with_key(self, key_id: str, plaintext: bytes) -> bytes:
        """Encrypt data using a managed key."""
        key = self.get_key(key_id)
        if not key:
            raise ValueError(f"Key {key_id} not found or unwrap failed")
        from app.utils.crypto import SM4Cipher
        cipher = SM4Cipher(key)
        ciphertext, nonce, tag = cipher.encrypt_gcm(plaintext)
        return nonce + tag + ciphertext

    def decrypt_with_key(self, key_id: str, ciphertext: bytes) -> bytes:
        """Decrypt data using a managed key."""
        key = self.get_key(key_id)
        if not key:
            raise ValueError(f"Key {key_id} not found or unwrap failed")
        nonce, tag, ct = ciphertext[:12], ciphertext[12:28], ciphertext[28:]
        from app.utils.crypto import SM4Cipher
        cipher = SM4Cipher(key)
        return cipher.decrypt_gcm(ct, nonce, tag)

    def rotate_key(self, key_id: str, cert_public_key: str | None = None) -> str:
        """Rotate a key — generate new DEK, wrap with KEK, return new key_id.

        P0-8: If cert_public_key is provided, also SM2-encrypt the new DEK.

        Note: does not re-encrypt data already encrypted with the old key.
        Callers must handle data re-encryption separately.
        """
        new_key_id = f"{key_id}-rotated-{uuid.uuid4().hex[:8]}"
        if key_id in self._wrapped_keys:
            new_key_bytes = os.urandom(32)
            wrapped = self._hsm.wrap_dek(_KEK_ID, new_key_bytes)
            self._wrapped_keys[new_key_id] = wrapped

            # P0-8: SM2-encrypt rotated key if cert provided
            if cert_public_key:
                sm2_ct = crypto_service.sm2_encrypt(new_key_bytes, cert_public_key)
                self._sm2_encrypted_keys[new_key_id] = sm2_ct
                self._cert_public_keys[new_key_id] = cert_public_key

            logger.info(f"[KMS] Rotated key {key_id} → {new_key_id}")
        return new_key_id

    def generate_session_key(self, session_id: str) -> dict:
        """Generate a session key for a sandbox session.

        Session keys are short-lived, wrapped with KEK, and destroyed
        when the session ends. Plaintext is only returned for immediate
        distribution via env var — never persisted.

        Returns {key_id, key_bytes}.
        """
        self._ensure_kek()
        key_id = f"session-{session_id}-{uuid.uuid4().hex[:8]}"
        key_bytes = os.urandom(32)

        wrapped = self._hsm.wrap_dek(_KEK_ID, key_bytes)
        self._wrapped_keys[key_id] = wrapped
        logger.info(f"[KMS] Generated session key {key_id}")
        return {"key_id": key_id, "key_bytes": key_bytes}

    def distribute_key(
        self,
        key_id: str,
        session_id: str,
        attestation: bytes | None = None,
        tee_quote: "TEEQuote | None" = None,
    ) -> str | None:
        """Return session key hex for in-memory injection into sandbox.

        Key is passed via environment variable (CDS_SESSION_KEY) at execution
        time — never written to disk. Returns hex string or None if key not found.

        P0-9 / gap B1: fail-closed attestation gate. When
        ``CDS_KMS_REQUIRE_ATTESTATION=true`` (default), distribution without
        any attestation evidence is rejected — the previous behaviour of
        silently handing out keys when no quote was supplied defeated P0-9.

        Args:
            key_id: Key to distribute
            session_id: Target session
            attestation: Raw TEE quote bytes (P0-9)
            tee_quote: Parsed TEEQuote object (P0-9, alternative to raw bytes)
        """
        # P0-9 / gap B1: unattested distribution gate (fail-closed)
        if not (attestation or tee_quote):
            from app.core.config import get_settings
            if get_settings().KMS_REQUIRE_ATTESTATION:
                logger.warning(
                    "[KMS] Rejected UNATTESTED key distribution (key=%s, session=%s): "
                    "no TEE quote provided and KMS_REQUIRE_ATTESTATION is enabled",
                    key_id, session_id,
                )
                return None

        # P0-9: Verify TEE attestation if provided
        if attestation or tee_quote:
            verified = self._verify_attestation(attestation, tee_quote, key_id, session_id)
            if not verified:
                logger.warning(f"[KMS] TEE attestation failed for key {key_id}, session {session_id}")
                return None

        wrapped = self._wrapped_keys.get(key_id)
        if not wrapped:
            return None
        try:
            plaintext = self._hsm.unwrap_dek(_KEK_ID, wrapped)
            return plaintext.hex()
        except Exception as e:
            logger.error(f"[KMS] Failed to unwrap session key {key_id}: {e}")
            return None

    def _verify_attestation(
        self,
        attestation: bytes | None,
        tee_quote: "TEEQuote | None",
        key_id: str,
        session_id: str,
    ) -> bool:
        """Verify TEE attestation before key distribution (P0-9).

        Checks:
        1. Quote structure and signature
        2. MRENCLAVE measurement against whitelist
        3. Quote freshness (within max_quote_age_seconds)
        4. TCB version check

        Returns True if attestation is valid, False otherwise.
        """
        from app.services.remote_attestation import (
            AttestationService, AttestationPolicy, TEEQuote,
            QuoteType, TEEType as _TT,
        )
        from app.utils.crypto import sm3_hash
        import json as _json

        try:
            if tee_quote is None and attestation:
                # Parse raw bytes into TEEQuote
                parsed = _json.loads(attestation)
                quote_type_str = parsed.get("type", "software_hash")
                quote_type_map = {
                    "sgx_ecdsa": QuoteType.SGX_ECDSA,
                    "sev_snp": QuoteType.SEV_SNP,
                    "software_hash": QuoteType.SOFTWARE_HASH,
                }
                tee_type_map = {
                    "sgx_ecdsa": _TT.SGX,
                    "sev_snp": _TT.SEV_SNP,
                    "software_hash": _TT.FIRECRACKER,
                }
                tee_quote = TEEQuote(
                    quote_id=parsed.get("quote_id", f"parsed-{uuid.uuid4().hex[:8]}"),
                    quote_type=quote_type_map.get(quote_type_str, QuoteType.SOFTWARE_HASH),
                    tee_type=tee_type_map.get(quote_type_str, _TT.FIRECRACKER),
                    raw_bytes=attestation,
                    measurement=parsed.get("measurement", ""),
                    report_data=parsed.get("report_data", ""),
                    tcb_version=parsed.get("tcb_version", ""),
                )

            if tee_quote is None:
                return False

            # Gap B2/T12: reject software-simulated quotes when simulation is
            # disallowed (production TEE posture) — the simulator's hash quote
            # is not hardware-backed evidence.
            if tee_quote.quote_type == QuoteType.SOFTWARE_HASH:
                from app.core.config import get_settings
                if not get_settings().ALLOW_SIMULATION:
                    logger.warning(
                        "[KMS] Rejected software-simulated attestation (key=%s, session=%s) "
                        "because ALLOW_SIMULATION=false",
                        key_id, session_id,
                    )
                    return False

            policy = AttestationPolicy(require_fresh_quote=True, max_quote_age_seconds=300)
            service = AttestationService(policy=policy)
            result = service.verify_quote(tee_quote)

            if result.verified:
                logger.info(
                    f"[KMS] TEE attestation verified for key {key_id}: "
                    f"type={tee_quote.tee_type.value}, measurement={tee_quote.measurement[:16]}..."
                )
            else:
                logger.warning(
                    f"[KMS] TEE attestation failed for key {key_id}: "
                    f"status={result.status.value}, error={result.error_message}"
                )

            return result.verified

        except Exception as e:
            logger.error(f"[KMS] TEE attestation verification error: {e}")
            return False

    def destroy_key(self, key_id: str) -> bool:
        """Destroy a key — removes wrapped blob and SM2 ciphertext from store.

        Returns True if key was found and destroyed.
        """
        found = False
        if key_id in self._wrapped_keys:
            del self._wrapped_keys[key_id]
            found = True
        if key_id in self._sm2_encrypted_keys:
            del self._sm2_encrypted_keys[key_id]
            found = True
        if key_id in self._cert_public_keys:
            del self._cert_public_keys[key_id]
        if found:
            logger.info(f"[KMS] Destroyed key {key_id}")
        return found

    def export_wrapped(self, key_id: str) -> bytes | None:
        """Return a copy of the KEK-wrapped key blob for persistence (gap B3).

        The blob alone is useless without the KEK held in HSM/Vault — this is
        envelope-encryption safe. Returns None when the key is not in memory.
        """
        blob = self._wrapped_keys.get(key_id)
        return bytes(blob) if blob is not None else None

    def export_sm2_ciphertext(self, key_id: str) -> bytes | None:
        """Return a copy of the SM2-encrypted DEK blob for persistence."""
        blob = self._sm2_encrypted_keys.get(key_id)
        return bytes(blob) if blob is not None else None

    def import_wrapped(self, key_id: str, wrapped: bytes, sm2_ciphertext: bytes | None = None) -> bool:
        """Restore a persisted wrapped key blob into the in-memory store (gap B3).

        Returns True when the blob was imported.
        """
        if not key_id or not wrapped:
            return False
        self._wrapped_keys[key_id] = bytes(wrapped)
        if sm2_ciphertext:
            self._sm2_encrypted_keys[key_id] = bytes(sm2_ciphertext)
        logger.info(f"[KMS] Imported wrapped key {key_id} from persistence")
        return True

    async def cleanup_expired_distributions(self, db) -> int:
        """Revoke expired key distributions (TTL enforcement).

        Queries KeyDistribution records where expires_at < now and status == ACTIVE,
        then marks them as EXPIRED and terminates associated sessions.

        Returns count of expired distributions.
        """
        from datetime import datetime, timezone
        from sqlalchemy import select, update
        from app.models.kms import KeyDistribution, DistributionStatus

        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(KeyDistribution).where(
                KeyDistribution.status == DistributionStatus.ACTIVE.value,
                KeyDistribution.expires_at.isnot(None),
                KeyDistribution.expires_at < now,
            )
        )
        expired = result.scalars().all()
        count = 0

        for dist in expired:
            dist.status = DistributionStatus.EXPIRED.value
            # Terminate associated session if exists
            if dist.session_id:
                try:
                    from app.services.sandbox_runtime import sandbox_runtime
                    await sandbox_runtime.terminate_sessions_by_key(
                        dist.key_id, db, reason="key_ttl_expired",
                    )
                except Exception as e:
                    logger.warning(f"[KMS] Failed to terminate session for expired dist {dist.id}: {e}")

            logger.info(f"[KMS] Expired key distribution {dist.id} (key={dist.key_id}, session={dist.session_id})")
            count += 1

        if count > 0:
            from app.services.audit_service import audit_service
            await audit_service.log(
                db, action="kms.ttl_cleanup",
                resource_type="key_distribution",
                detail={"expired_count": count, "cleanup_time": now.isoformat()},
            )
            await db.flush()
            logger.info(f"[KMS] TTL cleanup: {count} distributions expired")

        return count


# Singleton
kms_service = KMSService()


async def _ttl_cleanup_loop():
    """Background loop: periodically clean up expired key distributions."""
    import asyncio
    from app.core.database import async_session

    while True:
        try:
            await asyncio.sleep(300)  # Every 5 minutes
            async with async_session() as db:
                await kms_service.cleanup_expired_distributions(db)
                await db.commit()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[KMS] TTL cleanup error: {e}")
            await asyncio.sleep(60)  # Back off on error
