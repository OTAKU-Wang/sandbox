"""Certificate Service — SM2 PKI certificate lifecycle management.

Handles certificate issuance, revocation, chain validation,
and CRL (Certificate Revocation List) generation.

P0-1: Builds real X.509v3 DER-encoded certificates signed with SM2-with-SM3.
"""
import base64
import os
import uuid
import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.certificate import Certificate, CertType, CertStatus
from app.services.crypto_service import crypto_service
from app.services.audit_service import audit_service

logger = logging.getLogger(__name__)


# ── ASN.1 DER encoding helpers (P0-1) ──────────────────────────────

def _int_to_bytes(n: int) -> bytes:
    return n.to_bytes((n.bit_length() + 7) // 8, 'big') if n else b'\x00'


def _encode_length(length: int) -> bytes:
    if length < 0x80:
        return bytes([length])
    elif length < 0x100:
        return bytes([0x81, length])
    elif length < 0x10000:
        return bytes([0x82, length >> 8, length & 0xff])
    else:
        return bytes([0x83, length >> 16, (length >> 8) & 0xff, length & 0xff])


def _encode_tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + _encode_length(len(value)) + value


def _encode_sequence(*items: bytes) -> bytes:
    return _encode_tlv(0x30, b''.join(items))


def _encode_set(*items: bytes) -> bytes:
    return _encode_tlv(0x31, b''.join(items))


def _encode_integer(n: int | bytes) -> bytes:
    if isinstance(n, int):
        b = _int_to_bytes(n)
        if b and b[0] & 0x80:
            b = b'\x00' + b
    else:
        b = n
        if b and b[0] & 0x80:
            b = b'\x00' + b
    return _encode_tlv(0x02, b)


def _encode_bitstring(data: bytes) -> bytes:
    return _encode_tlv(0x03, b'\x00' + data)


def _encode_octetstring(data: bytes) -> bytes:
    return _encode_tlv(0x04, data)


def _encode_oid(oid_str: str) -> bytes:
    parts = [int(x) for x in oid_str.split('.')]
    encoded = bytes([40 * parts[0] + parts[1]])
    for p in parts[2:]:
        if p < 0x80:
            encoded += bytes([p])
        else:
            chunks = []
            chunks.append(p & 0x7f)
            p >>= 7
            while p:
                chunks.append(0x80 | (p & 0x7f))
                p >>= 7
            encoded += bytes(reversed(chunks))
    return _encode_tlv(0x06, encoded)


def _encode_utf8string(s: str) -> bytes:
    return _encode_tlv(0x0c, s.encode('utf-8'))


def _encode_printablestring(s: str) -> bytes:
    return _encode_tlv(0x13, s.encode('ascii'))


def _encode_utctime(dt: datetime) -> bytes:
    return _encode_tlv(0x17, dt.strftime('%y%m%d%H%M%SZ').encode('ascii'))


def _encode_explicit(tag: int, *items: bytes) -> bytes:
    return _encode_tlv(0xa0 | tag, b''.join(items))


def _encode_null() -> bytes:
    return bytes([0x05, 0x00])


def _encode_boolean(value: bool) -> bytes:
    """BOOLEAN with the DER value octets (0xFF true / 0x00 false).

    A104: BasicConstraints used to carry ``INTEGER 1`` where ``BOOLEAN`` is required, so a
    strict parser read ``ca=FALSE`` (or refused the extension outright) on a real CA cert.
    """
    return _encode_tlv(0x01, b'\xff' if value else b'\x00')


# RFC 5280 §4.2.1.3 KeyUsage bit assignments, numbered from the MOST significant bit of the
# first content octet — the numbering a DER BIT STRING actually uses.
_KEY_USAGE_BITS: tuple[str, ...] = (
    'digitalSignature', 'nonRepudiation', 'keyEncipherment', 'dataEncipherment',
    'keyAgreement', 'keyCertSign', 'cRLSign', 'encipherOnly', 'decipherOnly',
)


def _encode_key_usage(*names: str) -> bytes:
    """Encode a KeyUsage BIT STRING (A104).

    The previous encoding wrote the bit names as LOW-order bits with ``unused bits = 1``,
    which is both invalid DER (the trailing unused bit was set) and semantically wrong: an
    end-entity certificate asked for digitalSignature+keyEncipherment came out declaring
    keyCertSign+encipherOnly, i.e. it could sign other certificates.
    """
    unknown = [name for name in names if name not in _KEY_USAGE_BITS]
    if unknown:
        raise ValueError(f"unknown KeyUsage names: {unknown}")
    if not names:
        return _encode_tlv(0x03, b'\x07')
    last_bit = max(_KEY_USAGE_BITS.index(name) for name in names)
    octets = bytearray((last_bit // 8) + 1)
    for name in names:
        bit = _KEY_USAGE_BITS.index(name)
        octets[bit // 8] |= 0x80 >> (bit % 8)
    unused = (7 - last_bit) % 8
    return _encode_tlv(0x03, bytes([unused]) + bytes(octets))


def _decode_key_usage(value: bytes) -> list[str]:
    """Read back a KeyUsage BIT STRING (``value`` is the extension octet string)."""
    if value and value[0] == 0x03:  # the extension value is a BIT STRING TLV
        header = 2
        if value[1] & 0x80:
            header = 2 + (value[1] & 0x7F)
        value = value[header:]
    if len(value) < 2 or value[0] > 7:
        return []
    content = value[1:]
    used_bits = len(content) * 8 - value[0]
    names: list[str] = []
    for bit in range(min(used_bits, len(_KEY_USAGE_BITS))):
        if content[bit // 8] & (0x80 >> (bit % 8)):
            names.append(_KEY_USAGE_BITS[bit])
    return names


def _encode_ecdsa_sig_value(r_s: bytes) -> bytes:
    """Wrap a raw ``r||s`` SM2 signature into DER ``ECDSA-Sig-Value`` (A104).

    GM/T 0006 / RFC 5758 put the signature value in
    ``SEQUENCE { r INTEGER, s INTEGER }``; emitting the bare 64 bytes made every strict X.509
    parser (and OpenSSL/BabaSSL on the real TLCP handshake) reject the certificate, while the
    hand-rolled parser next door happily accepted it — so the platform kept issuing PEMs it
    believed were fine.
    """
    if len(r_s) != 64:
        raise ValueError(f"expected a 64-byte raw SM2 signature, got {len(r_s)} bytes")
    r = int.from_bytes(r_s[:32], 'big')
    s = int.from_bytes(r_s[32:], 'big')
    return _encode_sequence(_encode_integer(r), _encode_integer(s))


def _decode_ecdsa_sig_value(sig_bits: bytes) -> bytes:
    """Inverse of :func:`_encode_ecdsa_sig_value`: DER SEQUENCE{r,s} → raw ``r||s``.

    Already-bare input is passed through unchanged so certificates issued before this fix
    still verify.
    """
    if not sig_bits or sig_bits[0] != 0x30:
        return sig_bits

    def read(buf: bytes, at: int) -> tuple[int, bytes, int]:
        tag = buf[at]
        length = buf[at + 1]
        if length & 0x80:
            extra = length & 0x7F
            length = int.from_bytes(buf[at + 2:at + 2 + extra], 'big')
            start = at + 2 + extra
        else:
            start = at + 2
        return tag, buf[start:start + length], start + length

    _, body, _ = read(sig_bits, 0)
    items: list[tuple[int, bytes]] = []
    at = 0
    while at < len(body):
        tag, value, at = read(body, at)
        items.append((tag, value))
    if len(items) != 2 or any(tag != 0x02 for tag, _ in items):
        return sig_bits
    r, s = (int.from_bytes(value, 'big') for _, value in items)
    return r.to_bytes(32, 'big') + s.to_bytes(32, 'big')


def _build_name(dn: str) -> bytes:
    """Build X.500 Name from DN string like 'CN=foo,O=bar,C=CN'."""
    rdns = []
    for part in dn.split(','):
        part = part.strip()
        if '=' not in part:
            continue
        attr_type, attr_value = part.split('=', 1)
        attr_type = attr_type.strip()
        attr_value = attr_value.strip()
        oid_map = {
            'CN': '2.5.4.3', 'O': '2.5.4.10', 'OU': '2.5.4.11',
            'C': '2.5.4.6', 'ST': '2.5.4.8', 'L': '2.5.4.7',
            'SN': '2.5.4.5', 'DC': '0.9.2342.19200300.100.1.25',
        }
        oid = oid_map.get(attr_type.upper(), '2.5.4.3')
        atv = _encode_sequence(_encode_oid(oid), _encode_utf8string(attr_value))
        rdns.append(_encode_set(atv))
    return _encode_sequence(*rdns)


# SM2-with-SM3 signature algorithm OID: 1.2.156.10197.1.501
_SM2_SM3_SIG_OID = "1.2.156.10197.1.501"
# SM2 elliptic curve public key OID: 1.2.156.10197.1.301
_SM2_PUBKEY_OID = "1.2.156.10197.1.301"
# SM2 curve OID: 1.2.156.10197.1.301.1
_SM2_CURVE_OID = "1.2.156.10197.1.301.1"


def _as_aware(dt: datetime | None) -> datetime | None:
    """把 DB 读回的 naive datetime 归一为 UTC-aware（OCR-A106/A320）。

    SQLite 的 DateTime 列不携带时区，`datetime.now(timezone.utc) < naive` 直接
    TypeError → verify_chain/validate 在 SQLite 上必 500（fix-waves 记录的 A320
    同族）。naive 按 UTC 解释是本仓写入侧（一律 aware-utc 写入）的逆运算。
    """
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _pem_to_der(cert_pem: str) -> bytes:
    """PEM → DER bytes（raise ValueError on garbage）。"""
    import re

    pem_body = re.sub(r"-----.*?-----", "", cert_pem).strip()
    pem_body = re.sub(r"\s+", "", pem_body)
    try:
        return base64.b64decode(pem_body)
    except Exception as e:
        raise ValueError(f"invalid PEM base64: {e}") from e


class CertificateService:
    """SM2 PKI certificate lifecycle manager."""

    DEFAULT_VALIDITY_DAYS = 365
    CA_VALIDITY_DAYS = 3650  # 10 years for CA certs

    def __init__(self):
        # Local software key vault used when no external HSM is configured.
        # Private keys are intentionally not persisted in the certificate row.
        self._local_private_keys: dict[str, str] = {}

    async def issue_certificate(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        cert_type: str = CertType.IDENTITY.value,
        subject: str = "",
        validity_days: int | None = None,
        parent_cert_id: uuid.UUID | None = None,
    ) -> Certificate:
        """Issue a new SM2 certificate."""
        # Generate SM2 keypair
        keypair = crypto_service.generate_keypair()

        # Build certificate
        serial = f"CDS-{uuid.uuid4().hex[:16].upper()}"
        now = datetime.now(timezone.utc)
        days = validity_days or self.DEFAULT_VALIDITY_DAYS
        not_after = now + timedelta(days=days)

        if not subject:
            subject = f"CN={user_id},O=CDS,C=CN"

        issuer = "CN=CDS Root CA,O=CDS,C=CN"
        issuer_priv_key = None
        issuer_public_key = None
        if parent_cert_id:
            parent_result = await db.execute(
                select(Certificate).where(Certificate.id == parent_cert_id)
            )
            parent = parent_result.scalar_one_or_none()
            if not parent:
                raise ValueError(f"Parent certificate not found: {parent_cert_id}")
            if not parent.is_ca:
                raise ValueError(f"Parent certificate is not a CA: {parent_cert_id}")
            if parent.status != CertStatus.ACTIVE.value:
                raise ValueError(f"Parent certificate is not active: {parent_cert_id}")
            issuer = parent.subject
            issuer_priv_key = (
                self._local_private_keys.get(str(parent.id))
                or self._local_private_keys.get(parent.serial_number)
            )
            if not issuer_priv_key:
                raise ValueError(
                    "Issuer private key is not available in local key vault; "
                    "cannot issue a verifiable child certificate"
                )
            issuer_public_key = parent.public_key

        cert_pem = self._build_cert_pem(
            serial=serial,
            subject=subject,
            issuer=issuer,
            public_key=keypair.public_key,
            not_before=now,
            not_after=not_after,
            is_ca=(cert_type == CertType.PLATFORM.value),
            private_key=keypair.private_key,
            issuer_private_key=issuer_priv_key,
            issuer_public_key=issuer_public_key,
        )

        cert = Certificate(
            user_id=user_id,
            cert_type=cert_type,
            status=CertStatus.ACTIVE.value,
            serial_number=serial,
            subject=subject,
            issuer=issuer,
            public_key=keypair.public_key,
            certificate_pem=cert_pem,
            parent_cert_id=parent_cert_id,
            is_ca=(cert_type == CertType.PLATFORM.value),
            not_before=now,
            not_after=not_after,
        )
        db.add(cert)
        await db.flush()
        await db.refresh(cert)

        if getattr(cert, "id", None):
            self._local_private_keys[str(cert.id)] = keypair.private_key
        self._local_private_keys[serial] = keypair.private_key

        await audit_service.log(
            db,
            action="certificate.issue",
            resource_type="certificate",
            resource_id=str(cert.id),
            user_id=user_id,
            detail={
                "serial": serial,
                "cert_type": cert_type,
                "subject": subject,
                "valid_until": not_after.isoformat(),
            },
        )

        logger.info(f"Issued certificate {serial} for user {user_id}")
        return cert

    async def revoke_certificate(
        self,
        db: AsyncSession,
        cert_id: uuid.UUID,
        reason: str = "unspecified",
        revoked_by: uuid.UUID | None = None,
    ) -> Certificate:
        """Revoke a certificate (add to CRL)."""
        result = await db.execute(
            select(Certificate).where(Certificate.id == cert_id)
        )
        cert = result.scalar_one_or_none()
        if not cert:
            raise ValueError(f"Certificate not found: {cert_id}")
        if cert.status == CertStatus.REVOKED.value:
            raise ValueError(f"Certificate already revoked: {cert_id}")

        cert.status = CertStatus.REVOKED.value
        cert.revoked_at = datetime.now(timezone.utc)
        cert.revoke_reason = reason

        await db.flush()
        await db.refresh(cert)

        await audit_service.log(
            db,
            action="certificate.revoke",
            resource_type="certificate",
            resource_id=str(cert.id),
            user_id=revoked_by,
            detail={"serial": cert.serial_number, "reason": reason},
        )

        logger.info(f"Revoked certificate {cert.serial_number}: {reason}")

        # P0-10: Cascade revocation to dependent DEKs and sessions
        await self._cascade_revoke(db, cert, reason)

        return cert

    async def suspend_sessions_by_cert(self, db: AsyncSession, cert_id: uuid.UUID, reason: str = "cert_revoked") -> int:
        """Public API: suspend all sessions dependent on a certificate (P0-10).

        Returns the number of sessions actually terminated via the
        cert → DEKs → sessions cascade (OCR-A106: 旧实现恒返 1——「证书存在」被
        冒充成「会话受影响数」；现在返回真实处置计数，级联失败不再被粉饰)。
        """
        from sqlalchemy import select
        result = await db.execute(select(Certificate).where(Certificate.id == cert_id))
        cert = result.scalar_one_or_none()
        if not cert:
            return 0
        summary = await self._cascade_revoke(db, cert, reason)
        return int(summary.get("sessions_terminated", 0))

    async def _cascade_revoke(self, db: AsyncSession, cert: Certificate, reason: str) -> dict:
        """Cascade certificate revocation to dependent keys and sessions.

        Flow: revoke cert → find DEKs encrypted with this cert → suspend them
              → terminate active sessions using those keys → audit all actions.

        OCR-A106 诚实化：会话终止失败过去被 except 吞掉后仍记
        ``certificate.cascade.complete``——审计在级联实际未完成时谎报完成。
        失败现在如实入账：动作名 ``certificate.cascade.incomplete`` + 错误明细，
        并把结构化摘要返回给调用方。
        """
        from app.models.kms import DataEncryptionKey, DEKStatus

        # Find DEKs encrypted with this certificate
        dek_result = await db.execute(
            select(DataEncryptionKey).where(
                DataEncryptionKey.encryption_cert_id == cert.id,
                DataEncryptionKey.status == DEKStatus.ACTIVE.value,
            )
        )
        affected_deks = list(dek_result.scalars().all())

        if not affected_deks:
            logger.info(f"[CertRevoke] No DEKs linked to cert {cert.serial_number}")
            return {"deks_suspended": 0, "sessions_terminated": 0, "termination_error": None}

        suspended_key_ids = []
        for dek in affected_deks:
            dek.status = DEKStatus.REVOKED.value
            dek.revoked_at = datetime.now(timezone.utc)
            suspended_key_ids.append(dek.key_id)

            await audit_service.log(
                db,
                action="certificate.cascade.dek_suspended",
                resource_type="data_encryption_key",
                resource_id=str(dek.id),
                detail={
                    "cert_serial": cert.serial_number,
                    "key_id": dek.key_id,
                    "reason": f"Certificate revoked: {reason}",
                },
            )

        await db.flush()

        # Terminate active sessions using affected keys
        total_terminated = 0
        termination_error: str | None = None
        try:
            from app.services.sandbox_runtime import sandbox_runtime
            for key_id in suspended_key_ids:
                count = await sandbox_runtime.terminate_sessions_by_key(
                    key_id, db, reason=f"certificate_revoked:{cert.serial_number}"
                )
                total_terminated += count
        except Exception as e:
            # A106：不再「吞失败 + 记 complete」；错误进审计、进返回值。
            logger.error(f"[CertRevoke] Session termination failed: {e}")
            termination_error = f"{type(e).__name__}: {e}"

        cascade_complete = termination_error is None
        await audit_service.log(
            db,
            action="certificate.cascade.complete" if cascade_complete else "certificate.cascade.incomplete",
            resource_type="certificate",
            resource_id=str(cert.id),
            detail={
                "cert_serial": cert.serial_number,
                "deks_suspended": len(affected_deks),
                "sessions_terminated": total_terminated,
                "sessions_termination_error": termination_error,
                "cascade_complete": cascade_complete,
                "reason": reason,
            },
        )

        logger.info(
            f"[CertRevoke] Cascade {'complete' if cascade_complete else 'INCOMPLETE'} "
            f"for cert {cert.serial_number}: {len(affected_deks)} DEKs suspended, "
            f"{total_terminated} sessions terminated"
            + (f" (termination error: {termination_error})" if termination_error else "")
        )
        return {
            "deks_suspended": len(affected_deks),
            "sessions_terminated": total_terminated,
            "termination_error": termination_error,
        }

    async def get_user_certificates(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        cert_type: str | None = None,
        status: str | None = None,
    ) -> list[Certificate]:
        """Get certificates for a user."""
        query = select(Certificate).where(Certificate.user_id == user_id)
        if cert_type:
            query = query.where(Certificate.cert_type == cert_type)
        if status:
            query = query.where(Certificate.status == status)
        query = query.order_by(Certificate.created_at.desc())

        result = await db.execute(query)
        return list(result.scalars().all())

    async def validate_certificate(
        self,
        db: AsyncSession,
        cert_id: uuid.UUID,
    ) -> tuple[bool, str]:
        """Validate a certificate's current status.

        Returns: (is_valid, reason)
        """
        result = await db.execute(
            select(Certificate).where(Certificate.id == cert_id)
        )
        cert = result.scalar_one_or_none()
        if not cert:
            return False, "Certificate not found"

        if cert.status == CertStatus.REVOKED.value:
            return False, f"Certificate revoked: {cert.revoke_reason}"

        now = datetime.now(timezone.utc)
        not_before = _as_aware(cert.not_before)   # A320 同族：SQLite naive 列直比 aware 会 TypeError→500
        not_after = _as_aware(cert.not_after)
        if not_before and now < not_before:
            return False, "Certificate not yet valid"
        if not_after and now > not_after:
            return False, "Certificate expired"

        if cert.status == CertStatus.SUSPENDED.value:
            return False, "Certificate suspended"

        return True, "Certificate valid"

    async def generate_crl(
        self,
        db: AsyncSession,
        issuer: str | None = None,
    ) -> dict:
        """Generate a Certificate Revocation List.

        Returns CRL structure with revoked cert serials and timestamps.
        """
        query = select(Certificate).where(
            Certificate.status == CertStatus.REVOKED.value
        )
        if issuer:
            query = query.where(Certificate.issuer == issuer)

        result = await db.execute(query)
        revoked = result.scalars().all()

        now = datetime.now(timezone.utc)
        crl = {
            "issuer": issuer or "CN=CDS Root CA,O=CDS,C=CN",
            "this_update": now.isoformat(),
            "next_update": (now + timedelta(hours=24)).isoformat(),
            "revoked_certificates": [
                {
                    "serial": cert.serial_number,
                    "revocation_date": cert.revoked_at.isoformat() if cert.revoked_at else now.isoformat(),
                    "reason": cert.revoke_reason or "unspecified",
                }
                for cert in revoked
            ],
        }

        # Sign the CRL — OCR-A106 修复：旧实现每次调用现 generate_keypair() 签一把
        # 一次性密钥，验方永远拿不到那把公钥——「已签名」纯属装饰，比不签更坏
        # （它诱导消费方把撤销列表读成可信签发物）。现在只认本地密钥库里的签发 CA
        # 私钥：有则真签并披露 signed_by；无则显式 unsigned + reason，绝不伪造。
        import json

        crl["signed"] = False
        crl["signature"] = None
        issuer_cert = await self._find_issuer_ca(db, issuer)
        if issuer_cert is None:
            crl["signature_reason"] = (
                "no active issuer CA certificate found — CRL left unsigned "
                "(never signed with an ephemeral key nobody can verify against)"
            )
        else:
            priv = (
                self._local_private_keys.get(str(issuer_cert.id))
                or self._local_private_keys.get(issuer_cert.serial_number)
            )
            if not priv:
                crl["signature_reason"] = (
                    f"issuer CA {issuer_cert.serial_number} private key not available "
                    "in local key vault — CRL left unsigned"
                )
            else:
                try:
                    signable = {k: v for k, v in crl.items()
                                if k not in ("signed", "signature", "signed_by_serial", "signature_reason")}
                    crl_data = json.dumps(signable, sort_keys=True).encode()
                    signature = crypto_service.sign(crl_data, priv, issuer_cert.public_key)
                    crl["signature"] = signature.signature
                    crl["signed"] = True
                    crl["signed_by_serial"] = issuer_cert.serial_number
                except (TypeError, ValueError, RuntimeError, AttributeError) as e:
                    crl["signature_reason"] = (
                        f"issuer key present but CRL signing failed ({type(e).__name__}: {e}) "
                        "— CRL left unsigned"
                    )

        return crl

    async def _find_issuer_ca(self, db: AsyncSession, issuer: str | None) -> Certificate | None:
        """Find an active CA certificate that could legitimately sign the CRL."""
        query = select(Certificate).where(
            Certificate.is_ca.is_(True),
            Certificate.status == CertStatus.ACTIVE.value,
        )
        if issuer:
            query = query.where(Certificate.subject == issuer)
        else:
            query = query.where(Certificate.issuer == Certificate.subject)  # 自签根
        query = query.order_by(Certificate.not_before.desc())
        result = await db.execute(query)
        rows = list(result.scalars().all())
        return rows[0] if rows else None

    async def verify_chain(
        self,
        db: AsyncSession,
        cert_id: uuid.UUID,
    ) -> dict:
        """Verify the certificate chain of trust — cryptographically (OCR-A106 修复).

        旧实现只是「沿 parent_cert_id 取 DB 行比对 issuer/subject 字符串与状态列」，
        既不验任何签名也不校验信任锚：一行自签伪造就能冒充 CA 签发（自签仹即信），
        单行无 parent 的链还会直接 ``valid: true``。现在：

        1. 每一环用**父证书公钥**验子证书 TBS 的 SM2 签名（签名材料就是
           ``_build_cert_pem`` 签出去的那段 DER，父签发路径本就用父私钥签）；
        2. 根必须同时满足：PEM 回读 basicConstraints CA=TRUE、issuer==subject、
           **自签验签通过**——不再是「库里任意 is_ca 行」；
        3. 链必须终止于这样的根；无 parent 又非根 → ``no trust anchor`` 失效
           （旧实现在这里吐 valid true）；
        4. 有效期比较走 :func:`_as_aware`（SQLite naive 列直接比会 TypeError→500，
           即 fix-waves 记的 A320 同族）。
        """
        chain: list[dict] = []
        parsed_chain: list[dict] = []
        visited: set[uuid.UUID] = set()
        current_id = cert_id
        root_parsed: dict | None = None

        while current_id:
            if current_id in visited:
                return {"valid": False, "error": "Circular certificate chain detected", "chain": chain}
            visited.add(current_id)

            result = await db.execute(select(Certificate).where(Certificate.id == current_id))
            cert = result.scalar_one_or_none()
            if not cert:
                return {"valid": False, "error": f"Certificate {current_id} not found", "chain": chain}

            now = datetime.now(timezone.utc)
            not_before = _as_aware(cert.not_before)
            not_after = _as_aware(cert.not_after)
            is_expired = not_after < now if not_after else False
            is_revoked = cert.status == CertStatus.REVOKED.value
            is_not_yet_valid = not_before > now if not_before else False

            # 密码学输入不可得 = 无从验证 = fail-closed（不再只看状态列就放行）
            try:
                parsed = self._parse_x509_der(_pem_to_der(cert.certificate_pem or ""))
            except Exception as e:  # 解析器/解码抛什么都算「不可验」→ fail-closed
                return {
                    "valid": False,
                    "error": f"Certificate {cert.serial_number} has no parseable certificate_pem: {e}",
                    "chain": chain,
                }

            chain_entry = {
                "id": str(cert.id),
                "serial": cert.serial_number,
                "subject": cert.subject,
                "issuer": cert.issuer,
                "is_ca": cert.is_ca,
                "status": cert.status,
                "not_before": not_before.isoformat() if not_before else None,
                "not_after": not_after.isoformat() if not_after else None,
                "is_expired": is_expired,
                "is_revoked": is_revoked,
                "is_not_yet_valid": is_not_yet_valid,
                "signature_verified": False,
            }
            chain.append(chain_entry)
            parsed_chain.append(parsed)

            # Check validity
            if is_expired:
                return {"valid": False, "error": f"Certificate {cert.serial_number} is expired", "chain": chain}
            if is_revoked:
                return {"valid": False, "error": f"Certificate {cert.serial_number} is revoked", "chain": chain}
            if is_not_yet_valid:
                return {"valid": False, "error": f"Certificate {cert.serial_number} is not yet valid", "chain": chain}

            # Self-signed CA (issuer == subject) = potential root of trust — must PROVE it
            if parsed["issuer"] == parsed["subject"]:
                if not parsed.get("is_ca") or not cert.is_ca:
                    return {
                        "valid": False,
                        "error": f"Certificate {cert.serial_number} is self-signed but not a CA — cannot anchor a chain",
                        "chain": chain,
                    }
                if not self._verify_parsed_signature(parsed, parsed["public_key"]):
                    return {
                        "valid": False,
                        "error": f"Root certificate {cert.serial_number} fails self-signature verification",
                        "chain": chain,
                    }
                chain_entry["is_root"] = True
                chain_entry["signature_verified"] = True
                root_parsed = parsed
                break

            current_id = cert.parent_cert_id

        if root_parsed is None:
            # A106 核心补洞：旧实现让「无 parent 的任意单行」直接 valid true。
            return {
                "valid": False,
                "error": "Chain does not terminate at a self-signed CA root — no trust anchor",
                "chain": chain,
            }

        # Verify issuer consistency: each cert's issuer should match parent's subject
        for i in range(len(chain) - 1):
            child = chain[i]
            parent = chain[i + 1]
            if child["issuer"] != parent["subject"]:
                return {
                    "valid": False,
                    "error": f"Issuer mismatch at position {i}: '{child['issuer']}' != parent subject '{parent['subject']}'",
                    "chain": chain,
                }

        # A106 核心修复：每一环用父公钥对 PEM 内的 TBS 做真验签（父行 PEM 的 SPKI
        # 是密码学权威，不信任可被改写的 DB public_key 列）
        for i in range(len(chain) - 1):
            child_parsed = parsed_chain[i]
            parent_parsed = parsed_chain[i + 1]
            if not self._verify_parsed_signature(child_parsed, parent_parsed["public_key"]):
                return {
                    "valid": False,
                    "error": (
                        f"Signature verification failed at position {i}: certificate "
                        f"'{chain[i]['serial']}' was NOT signed by its parent key "
                        f"'{chain[i + 1]['serial']}' (self-forged link)"
                    ),
                    "chain": chain,
                }
            chain[i]["signature_verified"] = True

        return {
            "valid": True,
            "chain_length": len(chain),
            "chain": chain,
            "signatures_verified": True,
            "trust_anchor": {"serial": chain[-1]["serial"], "type": "self-signed-root", "self_signature": "verified"},
        }

    @staticmethod
    def _verify_parsed_signature(parsed: dict, issuer_public_key_hex: str) -> bool:
        """用给定公钥验 parsed 证书的 TBS 签名；任何异常/缺失都算验签失败（fail-closed）。"""
        try:
            tbs_der = parsed.get("tbs_certificate_der")
            sig_hex = parsed.get("signature") or ""
            if not tbs_der or not sig_hex or not issuer_public_key_hex:
                return False
            return bool(crypto_service.verify(tbs_der, sig_hex, issuer_public_key_hex))
        except (TypeError, ValueError, RuntimeError, AttributeError):
            return False

    def verify_certificate(self, cert_pem: str) -> dict:
        """Parse X.509 DER certificate and verify SM2 signature (P0-1).

        Returns dict with parsed fields and signature verification result.

        OCR-A106 诚实化：本函数只做「TBS 对证书内嵌公钥」的自洽验签——它证明的是
        「这张证书是自己签的」，不是「它被可信 CA 签发」。旧结果把 True 读成可信就是
        「自签仹即信」；现在随结论发出 ``self_signed`` / ``trusted`` /
        （签名自洽却谎称他人签发时的）``selfsigned_despite_issuer_claim`` 三件披露，
        信任判定只属于 :meth:`verify_chain`。公钥不再做脆弱的 startswith("04") 预剥
        （x 恰以 04 开头时会错剥两位），归一交给 crypto_service.verify。
        """
        try:
            cert_der = _pem_to_der(cert_pem)
        except ValueError as e:
            return {"valid": False, "error": f"Invalid PEM: {e}"}

        # Parse DER structure
        try:
            parsed = self._parse_x509_der(cert_der)
        except Exception as e:
            return {"valid": False, "error": f"X.509 parse failed: {e}"}

        # Verify SM2 signature against the embedded subject key (self-consistency ONLY)
        try:
            tbs_der = parsed["tbs_certificate_der"]
            sig_hex = parsed["signature"]
            pub_key = parsed["public_key"]

            if sig_hex and pub_key:
                valid = crypto_service.verify(tbs_der, sig_hex, pub_key)
                parsed["signature_valid"] = valid
            else:
                parsed["signature_valid"] = False
                parsed["signature_error"] = "Missing signature or public key"
        except Exception as e:
            parsed["signature_valid"] = False
            parsed["signature_error"] = str(e)

        # A106 披露层：单证书端点永远给不出「可信」，只能给出自洽性与误报形状
        self_signed = parsed.get("issuer") == parsed.get("subject")
        parsed["self_signed"] = self_signed
        parsed["trusted"] = False  # 信任判定只属于 verify_chain（有父验签 + 根锚）
        if parsed.get("signature_valid") and not self_signed:
            # 签名是拿「自己的」私钥签的，issuer 字段却声称别人签发——正是
            # 「自签仹冒充 CA 签发」的形状，必须在结论里点名而不是静默 True。
            parsed["selfsigned_despite_issuer_claim"] = True

        return parsed

    def _parse_x509_der(self, data: bytes) -> dict:
        """Parse X.509 DER certificate into structured dict."""
        from datetime import datetime as _dt

        def read_tlv(buf, offset):
            tag = buf[offset]
            offset += 1
            length = buf[offset]
            offset += 1
            if length & 0x80:
                num_bytes = length & 0x7f
                length = int.from_bytes(buf[offset:offset+num_bytes], 'big')
                offset += num_bytes
            value = buf[offset:offset+length]
            return tag, value, offset + length

        def parse_sequence_items(buf):
            items = []
            offset = 0
            while offset < len(buf):
                tag, value, offset = read_tlv(buf, offset)
                items.append((tag, value))
            return items

        # Outer SEQUENCE: Certificate
        _, cert_body, _ = read_tlv(data, 0)
        items = parse_sequence_items(cert_body)

        # items[0] = TBSCertificate, items[1] = AlgorithmIdentifier, items[2] = SignatureValue
        tbs_inner = items[0][1]
        # Reconstruct full TBS DER (SEQUENCE tag + length + body) for signature verification
        tbs_der = bytes([0x30]) + _encode_length(len(tbs_inner)) + tbs_inner
        sig_value = items[2][1]

        # Parse TBSCertificate
        tbs_items = parse_sequence_items(tbs_inner)
        result = {
            "tbs_certificate_der": tbs_der,
            "signature_algorithm": "",
            "signature": "",
            "public_key": "",
            "serial_number": "",
            "subject": "",
            "issuer": "",
            "not_before": None,
            "not_after": None,
            "is_ca": False,
        }

        idx = 0
        tbs_buf = items[0][1]

        # version [0] EXPLICIT INTEGER.
        # A104: this used to read `tbs_buf[0] & 0xa0 == 0xa0`, which Python parses as
        # `tbs_buf[0] & (0xa0 == 0xa0)` = `tbs_buf[0] & 1` — always 0 for the 0xa0 tag, so the
        # block never ran and every following field was read one element early.
        if tbs_buf and tbs_buf[idx] == 0xA0:
            _, ver_val, end = read_tlv(tbs_buf, 0)
            _, ver_int, _ = read_tlv(ver_val, 0)
            result["version"] = int.from_bytes(ver_int, 'big') + 1
            idx = end
        else:
            result["version"] = 1

        # serialNumber INTEGER
        _, serial_bytes, idx = read_tlv(tbs_buf, idx)
        result["serial_number"] = int.from_bytes(serial_bytes, 'big')

        # signature AlgorithmIdentifier
        _, sig_alg_body, idx = read_tlv(tbs_buf, idx)
        sig_alg_items = parse_sequence_items(sig_alg_body)
        if sig_alg_items:
            oid_bytes = sig_alg_items[0][1]
            result["signature_algorithm"] = self._decode_oid(oid_bytes)

        # issuer Name (SEQUENCE of SET of SEQUENCE)
        _, issuer_raw, idx = read_tlv(tbs_buf, idx)
        result["issuer"] = self._decode_name(issuer_raw)

        # validity SEQUENCE
        _, validity_body, idx = read_tlv(tbs_buf, idx)
        val_items = parse_sequence_items(validity_body)
        if len(val_items) >= 2:
            result["not_before"] = self._decode_time(val_items[0][1])
            result["not_after"] = self._decode_time(val_items[1][1])

        # subject Name
        _, subject_raw, idx = read_tlv(tbs_buf, idx)
        result["subject"] = self._decode_name(subject_raw)

        # subjectPublicKeyInfo SEQUENCE
        _, spki_body, idx = read_tlv(tbs_buf, idx)
        spki_items = parse_sequence_items(spki_body)
        if len(spki_items) >= 2:
            # BITSTRING containing the public key point
            pk_bytes = spki_items[1][1]
            if pk_bytes and pk_bytes[0] == 0:
                pk_bytes = pk_bytes[1:]  # skip unused bits byte
            result["public_key"] = pk_bytes.hex()

        # extensions [3] EXPLICIT (A104: the same precedence bug made this whole block dead
        # code, and is_ca was "extension present" instead of the basicConstraints value)
        result["key_usage"] = []
        if idx < len(tbs_buf) and tbs_buf[idx] == 0xA3:
            _, ext_body, idx = read_tlv(tbs_buf, idx)
            for seq_tag, ext_list in parse_sequence_items(ext_body):
                if seq_tag != 0x30:
                    continue
                for ext_tag, ext_seq in parse_sequence_items(ext_list):
                    if ext_tag != 0x30:
                        continue
                    ext_parts = parse_sequence_items(ext_seq)
                    if not ext_parts or ext_parts[0][0] != 0x06:
                        continue
                    oid = self._decode_oid(ext_parts[0][1])
                    value = next(
                        (part[1] for part in ext_parts[1:] if part[0] == 0x04), b''
                    )
                    if oid == "2.5.29.19":  # basicConstraints
                        bc_body = next(
                            (body for tag, body in parse_sequence_items(value)
                             if tag == 0x30),
                            b'',
                        )
                        bc_items = parse_sequence_items(bc_body)
                        result["is_ca"] = bool(
                            bc_items and bc_items[0][0] == 0x01
                            and bc_items[0][1] != b'\x00'
                        )
                    elif oid == "2.5.29.15":  # keyUsage
                        result["key_usage"] = _decode_key_usage(value)

        # Signature value (from outer Certificate)
        sig_raw = items[2][1]
        if sig_raw and sig_raw[0] == 0:
            sig_raw = sig_raw[1:]  # skip unused bits byte
        # crypto_service.verify() wants the bare r||s, so unwrap the DER signature value.
        result["signature"] = _decode_ecdsa_sig_value(sig_raw).hex()

        return result

    @staticmethod
    def _decode_oid(data: bytes) -> str:
        """Decode DER-encoded OID to dotted string."""
        if not data:
            return ""
        first = data[0]
        parts = [first // 40, first % 40]
        val = 0
        for b in data[1:]:
            val = (val << 7) | (b & 0x7f)
            if not (b & 0x80):
                parts.append(val)
                val = 0
        return '.'.join(str(p) for p in parts)

    @staticmethod
    def _decode_name(data: bytes) -> str:
        """Decode X.500 Name DER to DN string."""
        parts = []
        offset = 0
        while offset < len(data):
            # SET
            if data[offset] != 0x31:
                break
            set_len = data[offset + 1]
            if set_len & 0x80:
                n = set_len & 0x7f
                set_len = int.from_bytes(data[offset+2:offset+2+n], 'big')
                offset += 2 + n
            else:
                offset += 2
            set_end = offset + set_len

            # SEQUENCE inside SET
            if data[offset] == 0x30:
                seq_len = data[offset + 1]
                if seq_len & 0x80:
                    n = seq_len & 0x7f
                    seq_len = int.from_bytes(data[offset+2:offset+2+n], 'big')
                    offset += 2 + n
                else:
                    offset += 2
                seq_end = offset + seq_len

                # OID
                if data[offset] == 0x06:
                    oid_len = data[offset + 1]
                    oid_bytes = data[offset+2:offset+2+oid_len]
                    offset += 2 + oid_len
                else:
                    offset = seq_end
                    continue

                # Value (UTF8String or PrintableString)
                if offset < seq_end:
                    val_tag = data[offset]
                    val_len = data[offset + 1]
                    if val_len & 0x80:
                        n = val_len & 0x7f
                        val_len = int.from_bytes(data[offset+2:offset+2+n], 'big')
                        offset += 2 + n
                    else:
                        offset += 2
                    val_bytes = data[offset:offset+val_len]
                    try:
                        val_str = val_bytes.decode('utf-8')
                    except Exception:
                        val_str = val_bytes.hex()

                    oid_map = {
                        '2.5.4.3': 'CN', '2.5.4.10': 'O', '2.5.4.11': 'OU',
                        '2.5.4.6': 'C', '2.5.4.8': 'ST', '2.5.4.7': 'L',
                    }
                    oid_str = CertificateService._decode_oid(oid_bytes)
                    attr = oid_map.get(oid_str, oid_str)
                    parts.append(f"{attr}={val_str}")

                offset = set_end
            else:
                offset = set_end

        return ','.join(parts)

    @staticmethod
    def _decode_time(data: bytes) -> datetime | None:
        """Decode UTCTime or GeneralizedTime DER."""
        try:
            s = data.decode('ascii')
            if len(s) == 13 and s.endswith('Z'):  # UTCTime: YYMMDDHHMMSSZ
                year = int(s[:2])
                year = year + 2000 if year < 50 else year + 1900
                return datetime(year, int(s[2:4]), int(s[4:6]),
                               int(s[6:8]), int(s[8:10]), int(s[10:12]))
            elif len(s) == 15 and s.endswith('Z'):  # GeneralizedTime: YYYYMMDDHHMMSSZ
                return datetime(int(s[:4]), int(s[4:6]), int(s[6:8]),
                               int(s[8:10]), int(s[10:12]), int(s[12:14]))
        except Exception:
            pass
        return None

    def _build_cert_pem(
        self,
        serial: str,
        subject: str,
        issuer: str,
        public_key: str,
        not_before: datetime,
        not_after: datetime,
        is_ca: bool = False,
        private_key: str | None = None,
        issuer_private_key: str | None = None,
        issuer_public_key: str | None = None,
    ) -> str:
        """Build a real X.509v3 DER→PEM certificate signed with SM2-with-SM3.

        Args:
            serial: Certificate serial number string
            subject: Subject DN (e.g. 'CN=user,O=CDS,C=CN')
            issuer: Issuer DN
            public_key: SM2 public key (128-char hex, uncompressed point)
            not_before: Certificate validity start
            not_after: Certificate validity end
            is_ca: Whether this is a CA certificate
            private_key: Subject's SM2 private key (for self-signed)
            issuer_private_key: Issuer's SM2 private key (for CA-signed)
            issuer_public_key: Issuer's SM2 public key (for CA-signed)
        """
        serial_int = int.from_bytes(
            serial.encode('utf-8')[:20].rjust(20, b'\x00'), 'big'
        )

        # Build SubjectPublicKeyInfo: SM2 public key
        # Point format: 04 || x || y (uncompressed, 65 bytes)
        pub_point_bytes = bytes.fromhex('04' + public_key[:128])
        spki = _encode_sequence(
            _encode_sequence(
                _encode_oid(_SM2_PUBKEY_OID),
                _encode_oid(_SM2_CURVE_OID),
            ),
            _encode_bitstring(pub_point_bytes),
        )

        # Build TBSCertificate
        version = _encode_explicit(0, _encode_integer(2))  # v3
        serial_der = _encode_integer(serial_int)
        sig_alg = _encode_sequence(_encode_oid(_SM2_SM3_SIG_OID), _encode_null())
        issuer_der = _build_name(issuer)
        subject_der = _build_name(subject)
        validity = _encode_sequence(
            _encode_utctime(not_before),
            _encode_utctime(not_after),
        )

        # Extensions: BasicConstraints (for CA), KeyUsage
        extensions_der = self._build_extensions(is_ca)
        tbs_extensions = _encode_explicit(3, extensions_der)

        tbs_cert = _encode_sequence(
            version,
            serial_der,
            sig_alg,
            issuer_der,
            validity,
            subject_der,
            spki,
            tbs_extensions,
        )

        # Sign TBSCertificate with SM2. Do not emit placeholder signatures:
        # an unverifiable certificate is worse than an issuance failure.
        signing_key = issuer_private_key or private_key
        signing_pub = issuer_public_key or public_key
        if not signing_key:
            raise ValueError("Certificate signing key is required")
        try:
            signature = crypto_service.sign(tbs_cert, signing_key, signing_pub)
            sig_bytes = bytes.fromhex(signature.signature)
            sig_value = _encode_bitstring(_encode_ecdsa_sig_value(sig_bytes))
        except (TypeError, AttributeError, ValueError, RuntimeError) as e:
            raise RuntimeError(f"Certificate signing failed: {e}") from e

        # Assemble full certificate
        cert_der = _encode_sequence(
            tbs_cert,
            sig_alg,
            sig_value,
        )

        # Convert to PEM
        encoded = base64.b64encode(cert_der).decode()
        lines = [encoded[i:i+64] for i in range(0, len(encoded), 64)]
        pem_body = "\n".join(lines)
        return f"-----BEGIN CERTIFICATE-----\n{pem_body}\n-----END CERTIFICATE-----"

    def _build_extensions(self, is_ca: bool) -> bytes:
        """Build X.509v3 extensions (BasicConstraints, KeyUsage) in strict DER.

        Both are marked critical (A104): RFC 5280 §4.2.1.9 requires basicConstraints to be
        critical in a CA certificate, §4.2.1.3 recommends the same for keyUsage, and a
        non-critical flag would let a peer that ignores extensions mint cert-signing rights.
        """
        if is_ca:
            bc_value = _encode_sequence(_encode_boolean(True))
            ku_value = _encode_key_usage('keyCertSign', 'cRLSign')
        else:
            # ca BOOLEAN has DEFAULT FALSE, so an empty SEQUENCE is the DER for CA=FALSE.
            bc_value = _encode_sequence()
            ku_value = _encode_key_usage('digitalSignature', 'keyEncipherment')
        bc_ext = _encode_sequence(
            _encode_oid("2.5.29.19"),  # basicConstraints
            _encode_boolean(True),     # critical
            _encode_octetstring(bc_value),
        )
        ku_ext = _encode_sequence(
            _encode_oid("2.5.29.15"),  # keyUsage
            _encode_boolean(True),     # critical
            _encode_octetstring(ku_value),
        )

        return _encode_sequence(bc_ext, ku_ext)


# Singleton
certificate_service = CertificateService()
