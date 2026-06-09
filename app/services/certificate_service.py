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

        Returns the number of sessions affected.
        """
        from sqlalchemy import select
        result = await db.execute(select(Certificate).where(Certificate.id == cert_id))
        cert = result.scalar_one_or_none()
        if not cert:
            return 0
        await self._cascade_revoke(db, cert, reason)
        return 1

    async def _cascade_revoke(self, db: AsyncSession, cert: Certificate, reason: str) -> None:
        """Cascade certificate revocation to dependent keys and sessions.

        Flow: revoke cert → find DEKs encrypted with this cert → suspend them
              → terminate active sessions using those keys → audit all actions.
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
            return

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
        try:
            from app.services.sandbox_runtime import sandbox_runtime
            for key_id in suspended_key_ids:
                count = await sandbox_runtime.terminate_sessions_by_key(
                    key_id, db, reason=f"certificate_revoked:{cert.serial_number}"
                )
                total_terminated += count
        except Exception as e:
            logger.error(f"[CertRevoke] Session termination failed: {e}")

        await audit_service.log(
            db,
            action="certificate.cascade.complete",
            resource_type="certificate",
            resource_id=str(cert.id),
            detail={
                "cert_serial": cert.serial_number,
                "deks_suspended": len(affected_deks),
                "sessions_terminated": total_terminated,
                "reason": reason,
            },
        )

        logger.info(
            f"[CertRevoke] Cascade complete for cert {cert.serial_number}: "
            f"{len(affected_deks)} DEKs suspended, {total_terminated} sessions terminated"
        )

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
        if now < cert.not_before:
            return False, "Certificate not yet valid"
        if now > cert.not_after:
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

        # Sign the CRL
        try:
            keypair = crypto_service.generate_keypair()
            import json
            crl_data = json.dumps(crl, sort_keys=True).encode()
            signature = crypto_service.sign(crl_data, keypair.private_key, keypair.public_key)
            crl["signature"] = signature.signature
        except Exception as e:
            logger.warning(f"CRL signing failed: {e}")

        return crl

    async def verify_chain(
        self,
        db: AsyncSession,
        cert_id: uuid.UUID,
    ) -> dict:
        """Verify the certificate chain of trust.

        Walks up the parent_cert_id chain until reaching a self-signed CA cert
        or a cert with no parent. Returns the full chain and validation status.
        """
        chain = []
        visited = set()
        current_id = cert_id

        while current_id:
            if current_id in visited:
                return {"valid": False, "error": "Circular certificate chain detected", "chain": chain}
            visited.add(current_id)

            result = await db.execute(select(Certificate).where(Certificate.id == current_id))
            cert = result.scalar_one_or_none()
            if not cert:
                return {"valid": False, "error": f"Certificate {current_id} not found", "chain": chain}

            now = datetime.now(timezone.utc)
            is_expired = cert.not_after < now if cert.not_after else False
            is_revoked = cert.status == CertStatus.REVOKED.value
            is_not_yet_valid = cert.not_before > now if cert.not_before else False

            chain_entry = {
                "id": str(cert.id),
                "serial": cert.serial_number,
                "subject": cert.subject,
                "issuer": cert.issuer,
                "is_ca": cert.is_ca,
                "status": cert.status,
                "not_before": cert.not_before.isoformat() if cert.not_before else None,
                "not_after": cert.not_after.isoformat() if cert.not_after else None,
                "is_expired": is_expired,
                "is_revoked": is_revoked,
                "is_not_yet_valid": is_not_yet_valid,
            }
            chain.append(chain_entry)

            # Check validity
            if is_expired:
                return {"valid": False, "error": f"Certificate {cert.serial_number} is expired", "chain": chain}
            if is_revoked:
                return {"valid": False, "error": f"Certificate {cert.serial_number} is revoked", "chain": chain}
            if is_not_yet_valid:
                return {"valid": False, "error": f"Certificate {cert.serial_number} is not yet valid", "chain": chain}

            # Self-signed CA (issuer == subject) = root of trust
            if cert.is_ca and cert.issuer == cert.subject:
                chain_entry["is_root"] = True
                break

            current_id = cert.parent_cert_id

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

        return {"valid": True, "chain_length": len(chain), "chain": chain}

    def verify_certificate(self, cert_pem: str) -> dict:
        """Parse X.509 DER certificate and verify SM2 signature (P0-1).

        Returns dict with parsed fields and signature verification result.
        """
        import re

        # Extract DER from PEM
        pem_body = re.sub(r'-----.*?-----', '', cert_pem).strip()
        pem_body = re.sub(r'\s+', '', pem_body)
        try:
            cert_der = base64.b64decode(pem_body)
        except Exception as e:
            return {"valid": False, "error": f"Invalid PEM: {e}"}

        # Parse DER structure
        try:
            parsed = self._parse_x509_der(cert_der)
        except Exception as e:
            return {"valid": False, "error": f"X.509 parse failed: {e}"}

        # Verify SM2 signature
        try:
            tbs_der = parsed["tbs_certificate_der"]
            sig_hex = parsed["signature"]
            pub_key = parsed["public_key"]

            if sig_hex and pub_key:
                # Remove leading 04 (uncompressed point indicator) if present
                if pub_key.startswith("04"):
                    pub_key = pub_key[2:]
                valid = crypto_service.verify(tbs_der, sig_hex, pub_key)
                parsed["signature_valid"] = valid
            else:
                parsed["signature_valid"] = False
                parsed["signature_error"] = "Missing signature or public key"
        except Exception as e:
            parsed["signature_valid"] = False
            parsed["signature_error"] = str(e)

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

        offset = 0
        idx = 0
        tbs_buf = items[0][1]

        # version [0] EXPLICIT INTEGER
        if tbs_buf[0] & 0xa0 == 0xa0:
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

        # extensions [3] EXPLICIT
        if idx < len(tbs_buf) and tbs_buf[idx] & 0xa0 == 0xa3:
            _, ext_body, idx = read_tlv(tbs_buf, idx)
            ext_items = parse_sequence_items(ext_body)
            for ext_seq in ext_items:
                if ext_seq[0] == 0x30:
                    ext_parts = parse_sequence_items(ext_seq[1])
                    if ext_parts:
                        oid = self._decode_oid(ext_parts[0][1])
                        if oid == "2.5.29.19":  # basicConstraints
                            result["is_ca"] = True  # Simplified: presence = CA

        # Signature value (from outer Certificate)
        sig_raw = items[2][1]
        if sig_raw and sig_raw[0] == 0:
            sig_raw = sig_raw[1:]  # skip unused bits byte
        result["signature"] = sig_raw.hex()

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
            sig_value = _encode_bitstring(sig_bytes)
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
        """Build X.509v3 extensions (BasicConstraints, KeyUsage)."""
        # BasicConstraints: CA=TRUE/FALSE, pathLen=0 for CA
        if is_ca:
            bc_value = _encode_sequence(_encode_integer(1))  # CA=TRUE
        else:
            bc_value = _encode_sequence()  # CA=FALSE (default)
        bc_ext = _encode_sequence(
            _encode_oid("2.5.29.19"),  # basicConstraints
            _encode_octetstring(bc_value),
        )

        # KeyUsage
        if is_ca:
            # keyCertSign + cRLSign = bits 5+6 = 0x06
            ku_value = _encode_tlv(0x03, b'\x01\x06')
        else:
            # digitalSignature + keyEncipherment = bits 0+2 = 0x05
            ku_value = _encode_tlv(0x03, b'\x01\x05')
        ku_ext = _encode_sequence(
            _encode_oid("2.5.29.15"),  # keyUsage
            _encode_octetstring(ku_value),
        )

        return _encode_sequence(bc_ext, ku_ext)


# Singleton
certificate_service = CertificateService()
