"""KMS service — key management with HSM envelope encryption.

DEKs are wrapped with a KEK (stored in HSM) before being held in memory.
The HSMAdapter (Vault Transit or software fallback) handles all KEK operations.
Session keys are never written to disk — only distributed via env var.

P0-8: DEKs are additionally SM2-encrypted with the provider's certificate public key.
This adds a second layer of protection — even if the KEK is compromised,
the attacker still needs the SM2 private key to recover the DEK.
"""
import logging
import threading
import uuid
import os

from hashlib import sha256

from app.services.hsm_adapter import hsm_adapter
from app.services.crypto_service import crypto_service

logger = logging.getLogger(__name__)

# Well-known KEK ID for wrapping DEKs
_KEK_ID = "cds-master-kek"
# OCR-A140: 认领探测用的哨兵明文 —— 不是密钥材料，只用来证明 HSM 里那把 KEK
# 真能 wrap/unwrap 往返（「存在即复用」的判据）。
_KEK_PROBE = b"cds-kek-claim-probe"

# A138: a key hand-off must be a row in key_distributions with a real expiry —
# the TTL sweep then crypto-erases it. One hour matches the session-key lifetime
# the sandbox grants; a config knob would need config.py (NEEDS-OWNER).
KEY_DISTRIBUTION_TTL_SECONDS = 3600
# Bounded so a stopped TTL loop can never grow memory without limit; the oldest
# un-persisted hand-offs are dropped with an ERROR, never silently.
_MAX_PENDING_DISTRIBUTIONS = 4096


class KMSService:
    """Key Management Service with HSM envelope encryption.

    Architecture:
      generate_data_key() → random DEK → hsm.wrap_dek(KEK, DEK) → store wrapped
                       optionally → sm2_encrypt(DEK, cert_pubkey) → store SM2 ciphertext
      get_key()           → retrieve wrapped → hsm.unwrap_dek(KEK, wrapped) → plaintext
      distribute_key()    → unwrap + return hex (with optional TEE attestation check)
                       → buffered key_distributions row flushed by the TTL loop (A138)
      destroy_key()       → erase from the in-memory store only

    A121 honesty note: the HSM/Vault adapters expose NO revoke primitive, so
    destroy_key() cannot revoke anything inside the HSM — the KEK keeps being
    able to unwrap any blob an operator may still hold. Anything stronger needs
    a real HSM key-policy API (hsm_adapter.py, NEEDS-OWNER).

    P0-8: Two-layer encryption — KEK wrapping + SM2 encryption with cert public key.
    P0-9: TEE attestation required before key distribution.
    """

    def __init__(self):
        self._hsm = hsm_adapter
        self._kek_lock = threading.Lock()
        self._wrapped_keys: dict[str, bytes] = {}  # wrapped DEK blobs
        self._sm2_encrypted_keys: dict[str, bytes] = {}  # SM2-encrypted DEK blobs
        self._cert_public_keys: dict[str, str] = {}  # key_id → SM2 public key used
        # OCR-A161: the three dicts above are process memory. A DEK only outlives a
        # restart if its caller persisted the wrapped blob — the one place boot
        # recovery reads is KeyMetadata.wrapped_payload (kms_recovery).
        # Whoever writes that row calls mark_persisted(); every other DEK is
        # memory-only and is reported by unpersisted_key_ids() instead of silently
        # dooming the ciphertext that references its key_id.
        self._persisted_key_ids: set[str] = set()
        self._durability_warned = False
        self._unrecoverable_imports = 0
        self._kek_ready = False
        # A138: key hand-offs buffered until the TTL loop persists them into
        # key_distributions (distribute_key is sync and holds no DB session).
        self._pending_distributions: list[dict] = []

    def _kek_usable(self) -> bool:
        """HSM 里是否已有一把本进程真能用的主密钥（OCR-A140「先查」）。

        `hsm_adapter` 没有 key_exists 接口，而认领所需的性质是「能 wrap 也能 unwrap
        回来」；任何异常（含 Vault 不可达）都算不可用 —— 探测自身绝不把降级说成成功。
        """
        try:
            probe = self._hsm.wrap_dek(_KEK_ID, _KEK_PROBE)
            return self._hsm.unwrap_dek(_KEK_ID, probe) == _KEK_PROBE
        except Exception:
            return False

    def _ensure_kek(self):
        """原子认领主密钥：已存在即复用，绝不以同一个 id 覆写（OCR-A140）。

        修复前是无锁 check-then-act：事件循环线程与 to_thread worker 同时读到
        `_kek_ready is False` 就各自调 `generate_kek("cds-master-kek")`，而软件 HSM 的
        `generate_kek` 无条件 `self._keys[key_id] = os.urandom(32)`
        （app/services/hsm_adapter.py:199）—— 后到的那次覆写让先前封存的 wrapped DEK
        永久解不开（unwrap 只记一条 error 就返回 None）。锁把认领收成一个临界区，
        双检让后来的线程复用结果；mint 后还要回读自检，不过就不认领
        （`_kek_ready` 保持 False），免得 DEK 被封进一把连自检都过不了的密钥。

        跨进程的认领需要一张 KEK 认领表（本仓没有；见 OCR-A140 证据 NOTE），而软件
        HSM 的 KEK 本就只存在于进程内存（A139 已披露），所以本临界区覆盖的正是
        缺陷实际发生的那个面。
        """
        if self._kek_ready:
            return
        with self._kek_lock:
            if self._kek_ready:
                return
            if self._kek_usable():
                self._kek_ready = True
                logger.info("[KMS] Master KEK already usable in HSM — reusing it")
                return
            try:
                self._hsm.generate_kek(_KEK_ID)
                if not self._kek_usable():
                    raise RuntimeError("KEK 认领后自检失败：wrap/unwrap 往返不通")
            except Exception as e:
                logger.error(f"[KMS] Failed to initialize KEK: {e}")
                from app.core.errors import CDSError

                raise CDSError(
                    "密管服务不可用，无法初始化主密钥（KEK）",
                    code="KMS_UNAVAILABLE",
                    http_status=503,
                    detail={"reason": str(e)},
                ) from e
            self._kek_ready = True
            logger.info("[KMS] Master KEK initialized in HSM")

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

        # OCR-A161: a DEK minted here is memory-only until somebody persists its
        # wrapped blob. Callers that write ciphertext keyed by this key_id must put
        # export_wrapped() into KeyMetadata.wrapped_payload in the same transaction —
        # otherwise one api restart makes that data permanently undecryptable and
        # nobody notices until a download raises. Say it at mint time, once.
        if not self._durability_warned:
            self._durability_warned = True
            logger.warning(
                f"[KMS] DEK {key_id} lives only in process memory: callers that persist "
                f"ciphertext under this key_id must write export_wrapped() into "
                f"KeyMetadata.wrapped_payload in the same transaction and call "
                f"mark_persisted(), or a restart makes that data permanently "
                f"undecryptable (OCR-A161)"
            )

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

    def wrap_dek_bytes(self, dek_bytes: bytes) -> bytes:
        """Wrap a caller-supplied DEK with the master KEK (envelope).

        Volume DEKs are generated by the trusted plugin, not the KMS — this
        wraps the provided material instead of generating a new one. Returns
        the KEK-wrapped blob (safe to persist); the plaintext is never stored.
        """
        self._ensure_kek()
        return self._hsm.wrap_dek(_KEK_ID, dek_bytes)

    def unwrap_wrapped(self, wrapped: bytes) -> bytes | None:
        """Unwrap a KEK-wrapped blob to plaintext with the master KEK.

        Returns plaintext bytes, or None on failure (fail-closed). Used by the
        volume-key distribute route for KEK-wrapped volume DEKs persisted in
        the volume_keys table (not the in-memory session-key store).
        """
        try:
            return self._hsm.unwrap_dek(_KEK_ID, wrapped)
        except Exception as e:
            logger.error(f"[KMS] Failed to unwrap volume-key wrapped blob: {e}")
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
        except Exception as e:
            logger.error(f"[KMS] Failed to unwrap session key {key_id}: {e}")
            return None
        # A138: this is THE key hand-off point (every TEE/business caller funnels
        # through here), so this is where the key_distributions ledger row is
        # minted. Buffered in-process because distribute_key is sync; the TTL
        # loop flushes within one sweep interval.
        self._record_distribution(
            key_id, session_id,
            quote=attestation,
            attestation_status="verified" if (attestation or tee_quote) else "not_required",
        )
        return plaintext.hex()

    def _record_distribution(self, key_id: str, session_id: str, *, quote: bytes | None, attestation_status: str) -> None:
        """Buffer a key hand-off for the key_distributions ledger (A138).

        ``session_key_enc`` holds the KEK-WRAPPED blob (at-rest safe, same class
        of material as the ``encrypted_key`` column), never plaintext — and the
        TTL sweep crypto-erases it at expiry, which is the whole point of the
        ``expires_at`` contract.
        """
        from datetime import datetime, timedelta, timezone

        if len(self._pending_distributions) >= _MAX_PENDING_DISTRIBUTIONS:
            dropped = self._pending_distributions.pop(0)
            logger.error(
                "[KMS] distribution ledger overflow: dropped oldest un-persisted hand-off "
                "(key=%s session=%s) — TTL loop not running?", dropped.get("key_id"), dropped.get("session_id"),
            )
        wrapped = self._wrapped_keys.get(key_id)
        self._pending_distributions.append({
            "key_id": key_id,
            "session_id": session_id,
            "tee_quote_hash": sha256(quote).hexdigest() if quote else None,
            "attestation_status": attestation_status,
            "session_key_enc": wrapped.hex() if wrapped else None,
            "expires_at": datetime.now(timezone.utc) + timedelta(seconds=KEY_DISTRIBUTION_TTL_SECONDS),
        })

    async def flush_pending_distributions(self, db) -> int:
        """Persist buffered hand-offs into key_distributions (A138).

        Called by the TTL loop (and directly by tests). The row is written as
        ACTIVE with a real ``expires_at`` so ``cleanup_expired_distributions``
        has something to enforce — before this existed the sweep queried a table
        with no writers and the advertised TTL was fiction.
        """
        from app.models.kms import KeyDistribution, DistributionStatus

        pending, self._pending_distributions = self._pending_distributions, []
        for rec in pending:
            db.add(KeyDistribution(
                key_id=rec["key_id"],
                session_id=rec["session_id"],
                status=DistributionStatus.ACTIVE.value,
                tee_quote_hash=rec["tee_quote_hash"],
                attestation_status=rec["attestation_status"],
                session_key_enc=rec["session_key_enc"],
                expires_at=rec["expires_at"],
            ))
        if pending:
            await db.flush()
        return len(pending)

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
        """Destroy a key — erase the wrapped blob and SM2 ciphertext from the
        in-memory store. DB rows are the caller's transaction (the revoke route
        crypto-erases them in the same flush, A121).

        A121 honesty: there is no HSM revoke to call — the adapters expose none.
        The KEK itself remains able to unwrap any blob that escaped before
        destruction; key-policy-level revocation inside the HSM is NEEDS-OWNER.

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
        self._persisted_key_ids.discard(key_id)
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

        The blob is accepted only if the current KEK can actually unwrap it. With the
        software HSM the KEK is process-local, so a row written by an earlier process
        can be present in the DB and still be permanently undecryptable; loading it
        and logging "Imported … from persistence" reports a recovery that did not
        happen (OCR-A139 — the compensation path of OCR-A161). Reject it and count it
        instead, so the operator sees an unrecoverable total rather than an INFO line
        that lies.

        Returns True when the blob was imported (i.e. this KEK can unwrap it).
        """
        if not key_id or not wrapped:
            return False
        if self.unwrap_wrapped(bytes(wrapped)) is None:
            self._unrecoverable_imports += 1
            logger.warning(
                f"[KMS] REJECTED wrapped key {key_id}: the current KEK cannot unwrap it "
                f"(unrecoverable={self._unrecoverable_imports}) — ciphertext persisted under "
                f"this key_id is permanently undecryptable; cross-restart recovery needs a "
                f"real Vault/HSM KEK (P-GAP-004)"
            )
            return False
        self._wrapped_keys[key_id] = bytes(wrapped)
        if sm2_ciphertext:
            self._sm2_encrypted_keys[key_id] = bytes(sm2_ciphertext)
        self.mark_persisted(key_id)
        logger.info(f"[KMS] Imported wrapped key {key_id} from persistence")
        return True

    # ─── 持久化状态（OCR-A161）──────────────────────────────────────────────

    def mark_persisted(self, key_id: str) -> None:
        """Record that this key's wrapped blob exists outside this process.

        Call it in the same transaction that writes
        ``KeyMetadata.wrapped_payload`` — that is the column
        ``kms_recovery.restore_wrapped_keys`` reads at boot.
        """
        if key_id:
            self._persisted_key_ids.add(key_id)

    def is_persisted(self, key_id: str) -> bool:
        """True when the wrapped blob survives this process (already persisted)."""
        return key_id in self._persisted_key_ids

    def unpersisted_key_ids(self) -> list[str]:
        """Memory-only DEK keys — ciphertext under them dies with the process.

        Session keys are excluded on purpose: they are crypto-erased when the
        session terminates and are never meant to outlive it.
        """
        return [
            key_id for key_id in self._wrapped_keys
            if key_id.startswith("dek-") and key_id not in self._persisted_key_ids
        ]

    def unrecoverable_import_count(self) -> int:
        """Blobs rejected at load time because this KEK cannot unwrap them (OCR-A139)."""
        return self._unrecoverable_imports

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
            # A138: expiry must be a crypto-erase, not a status flip. While the
            # wrapped blob sat on the row, "expired" was decoration — unwrap +
            # re-hand-out stayed one SELECT away. The blob goes NULL here.
            dist.session_key_enc = None
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
                # A138: flush buffered hand-offs FIRST so the sweep below can
                # enforce expiry on rows that actually exist.
                await kms_service.flush_pending_distributions(db)
                await kms_service.cleanup_expired_distributions(db)
                await db.commit()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[KMS] TTL cleanup error: {e}")
            await asyncio.sleep(60)  # Back off on error
