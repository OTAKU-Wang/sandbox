"""SM2/SM3 Cryptographic Service — Chinese national standard signing and verification.

Uses gmssl library for SM2 signature operations.
SM2 public key format: uncompressed point (04 + x + y, 128 hex chars)
SM2 private key: 64 hex chars (256-bit scalar)
"""
import hashlib
import logging
from dataclasses import dataclass

from app.core.errors import CDSError

logger = logging.getLogger(__name__)

# Try importing gmssl; hash operations fall back to SHA-256 if unavailable.
try:
    from gmssl import sm2 as _sm2
    from gmssl import sm3 as _sm3
    _HAS_GMSSL = True
except ImportError:
    _HAS_GMSSL = False
    logger.warning("gmssl not installed — SM2 sign/encrypt operations are unavailable; SM3 falls back to SHA-256")


@dataclass
class SM2KeyPair:
    """SM2 key pair (hex-encoded)."""
    private_key: str  # 64 hex chars
    public_key: str   # 128 hex chars (uncompressed point, no 04 prefix for gmssl)


@dataclass
class SM2Signature:
    """SM2 signature (hex-encoded r||s)."""
    signature: str    # 128 hex chars (r: 64 + s: 64)


class CryptoService:
    """SM2/SM3 cryptographic operations for contract signing and verification."""

    def generate_keypair(self) -> SM2KeyPair:
        """Generate an SM2 key pair using the SM2 curve parameters."""
        if not _HAS_GMSSL:
            raise RuntimeError("gmssl library not installed — cannot generate SM2 keys")

        import os
        ecc_table = _sm2.default_ecc_table
        n = int(ecc_table['n'], 16)

        # Generate private key: random integer in [1, n-1]
        while True:
            private_key_bytes = os.urandom(32)
            private_key_int = int.from_bytes(private_key_bytes, 'big')
            if 1 <= private_key_int < n:
                break

        private_key = format(private_key_int, '064x')

        # Compute public key = private_key * G
        crypt = _sm2.CryptSM2(private_key=private_key, public_key='')
        g_point = ecc_table['g']  # 128-char hex (x||y)
        pub_point = crypt._kg(private_key_int, g_point)
        public_key = pub_point[:128]  # Take x||y (drop trailing '1' from _kg)

        return SM2KeyPair(private_key=private_key, public_key=public_key)

    def sign(self, data: bytes, private_key: str, public_key: str) -> SM2Signature:
        """Sign data with SM2 private key.

        Args:
            data: Raw bytes to sign
            private_key: 64-char hex private key
            public_key: 128-char hex public key (uncompressed, no 04 prefix)
        """
        if not _HAS_GMSSL:
            raise RuntimeError("gmssl library not installed — cannot sign with SM2")

        import os
        signer = _sm2.CryptSM2(private_key=private_key, public_key=public_key)
        # SM2 signing requires a Z value (user ID hash) per GM/T 0009
        # Default user ID: "1234567812345678" (standard default)
        digest_hex = self._sm3_hash_with_z(data, public_key)
        digest_bytes = bytes.fromhex(digest_hex)
        # Generate random nonce K for each signature
        n = int(_sm2.default_ecc_table['n'], 16)
        while True:
            k_bytes = os.urandom(32)
            k_int = int.from_bytes(k_bytes, 'big')
            if 1 <= k_int < n:
                break
        k_hex = format(k_int, '064x')
        signature = signer.sign(digest_bytes, k_hex)
        return SM2Signature(signature=signature)

    def verify(self, data: bytes, signature: str, public_key: str) -> bool:
        """Verify SM2 signature against public key.

        Args:
            data: Original data that was signed
            signature: 128-char hex signature (r||s)
            public_key: 128-char hex public key (uncompressed, no 04 prefix)
        """
        if not _HAS_GMSSL:
            raise RuntimeError("gmssl library not installed — cannot verify SM2 signature")

        verifier = _sm2.CryptSM2(private_key="", public_key=public_key)
        digest_hex = self._sm3_hash_with_z(data, public_key)
        digest_bytes = bytes.fromhex(digest_hex)
        return verifier.verify(signature, digest_bytes)

    def sm3_hash(self, data: bytes) -> str:
        """Compute SM3 hash of data (64-char hex).

        The SHA-256 fallback for a missing gmssl goes through the single gated
        implementation in app.utils.crypto, so CDS_SM3_FALLBACK_ALLOWED=false
        raises here too (PZ-04/MR-A14). This is the function that hashes audit
        Merkle leaves, so an ungated fallback would keep minting SHA-256
        "SM3" roots in production while the gate looked enforced.
        """
        if _HAS_GMSSL:
            return _sm3.sm3_hash(list(data))
        from app.utils.crypto import sm3_hash as _gated_sm3_hash

        return _gated_sm3_hash(data)

    def _sm3_hash_with_z(self, data: bytes, public_key: str) -> str:
        """Compute SM3 hash with Z value (GM/T 0003.2 5.2 / GB 32918.2).

        Z = SM3(ENTL || ID_A || a || b || xG || yG || xA || yA)
        e = SM3(Z || M)

        曲线系数 a/b 属于 Z 的必输入（早期版本遗漏，导致本服务只能验自己签的名，
        浏览器 sm-crypto / HSM / 第三方验签工具等标准实现一律验不过）。
        曲线参数取自 gmssl 所用同一张表，与 EC 运算保持同源。
        """
        if not _HAS_GMSSL:
            return hashlib.sha256(data).hexdigest()

        # Default user ID: "1234567812345678" (16 bytes)
        user_id = b"1234567812345678"
        entlen = len(user_id) * 8  # bit length
        entl = entlen.to_bytes(2, 'big')

        # SM2 curve parameters (sm2p256v1), same table the EC operations use
        table = _sm2.default_ecc_table
        a = table["a"].rjust(64, "0")
        b = table["b"].rjust(64, "0")
        xG = table["g"][:64].rjust(64, "0")
        yG = table["g"][64:128].rjust(64, "0")

        # Public key coordinates
        if len(public_key) == 128:
            xA = public_key[:64]
            yA = public_key[64:]
        else:
            raise ValueError(f"Invalid SM2 public key length: {len(public_key)}")

        # Z = SM3(ENTL || ID_A || a || b || xG || yG || xA || yA)
        z_input = entl + user_id + bytes.fromhex(a + b + xG + yG + xA + yA)
        z_hash = _sm3.sm3_hash(list(z_input))

        # Final hash = SM3(Z || M)
        final_input = bytes.fromhex(z_hash) + data
        return _sm3.sm3_hash(list(final_input))

    def sm2_encrypt(self, plaintext: bytes, public_key: str) -> bytes:
        """Encrypt data with SM2 public key (GM/T 0003).

        Args:
            plaintext: Raw bytes to encrypt
            public_key: 128-char hex public key (uncompressed, no 04 prefix)

        Returns:
            SM2 ciphertext bytes (C1||C3||C2 format)
        """
        if not _HAS_GMSSL:
            raise RuntimeError("gmssl library not installed — cannot encrypt with SM2")

        crypt = _sm2.CryptSM2(private_key="", public_key=public_key)
        encrypted = crypt.encrypt(plaintext)
        # gmssl returns hex string, convert to bytes
        if isinstance(encrypted, str):
            return bytes.fromhex(encrypted)
        return encrypted

    def sm2_decrypt(self, ciphertext: bytes, private_key: str, public_key: str) -> bytes:
        """Decrypt SM2 ciphertext with private key (GM/T 0003).

        Args:
            ciphertext: SM2 ciphertext bytes (C1||C3||C2 format)
            private_key: 64-char hex private key
            public_key: 128-char hex public key (uncompressed, no 04 prefix)

        Returns:
            Decrypted plaintext bytes
        """
        if not _HAS_GMSSL:
            raise RuntimeError("gmssl library not installed — cannot decrypt with SM2")

        crypt = _sm2.CryptSM2(private_key=private_key, public_key=public_key)
        # gmssl expects hex string input
        ct_hex = ciphertext.hex() if isinstance(ciphertext, bytes) else ciphertext
        decrypted = crypt.decrypt(ct_hex)
        if isinstance(decrypted, str):
            return bytes.fromhex(decrypted)
        return decrypted

    # ─── 跨方签名原文的字节锁（OCR-A110 critical / OCR-A255）───────────────────
    # `|` 分隔字段、`,` 分隔 purpose_scope 元素。字段里出现分隔符就能拼出与另一组
    # 字段**完全相同**的原文（`contract_no="X|purpose:Y"` 与 `purpose="Y"` 同串），
    # 而这份原文的 SM2 签名就是双方的不可否认证据 —— 一条签名同时为两种互斥的用
    # 途限定背书。这里在拼接前**拒绝**而不是转义：原文形状同时被 cds-frontend 的
    # utils/crypto.ts contractSignPayload 逐字镜像（前端拿不到服务端私钥，必须先本地
    # 算出同一串字节），改编码得两端同改，转义最容易错成「各自转义」→ 验签静默失败。
    # 长度上限取 DB 列宽（app/models/contract.py：contract_no / purpose 均为
    # String(64)），所以任何合法落库值都不会被这条校验拒掉。
    _PAYLOAD_MAX_LEN = 64
    _PAYLOAD_ROLE_MAX_LEN = 16
    _PAYLOAD_SCOPE_MAX_ITEMS = 64

    @classmethod
    def _payload_field_issues(
        cls, field: str, value: object, *, max_len: int, forbidden: str = "|",
    ) -> list[dict]:
        """一个字段能否无歧义地进原文：不含分隔符、不超列宽。"""
        text = "" if value is None else str(value)
        issues = [{"field": field, "reason": f"contains forbidden separator {ch!r}"}
                  for ch in forbidden if ch in text]
        if len(text) > max_len:
            issues.append({"field": field, "reason": f"exceeds {max_len} characters"})
        return issues

    @staticmethod
    def contract_sign_data(contract_id: str, contract_no: str, party_role: str, timestamp: str,
                           purpose: str | None = None, purpose_scope: list | None = None) -> bytes:
        """Build the canonical sign payload for a contract.

        Gap A4: a purpose limitation is part of the signed payload so both
        parties commit to it. purpose=None keeps the legacy payload shape,
        so existing purpose-less contracts verify unchanged. ``purpose_scope=[]``
        and ``None`` denote the same bytes — an explicit empty scope adds no
        restriction, matching the frontend mirror's ``purposeScope.length > 0``.

        Raises CDSError(VALIDATION_ERROR, 422) when a field would make the payload
        ambiguous (an embedded ``|`` / `,`) or overlong: an ambiguous payload is one
        the counterparty can re-sign/reinterpret, so it is refused, never escaped
        (OCR-A110 / OCR-A255).
        """
        v = CryptoService._payload_field_issues
        errors: list[dict] = []
        errors += v("contract_id", contract_id, max_len=CryptoService._PAYLOAD_MAX_LEN)
        errors += v("contract_no", contract_no, max_len=CryptoService._PAYLOAD_MAX_LEN)
        errors += v("party_role", party_role, max_len=CryptoService._PAYLOAD_ROLE_MAX_LEN)
        errors += v("timestamp", timestamp, max_len=CryptoService._PAYLOAD_MAX_LEN)
        errors += v("purpose", purpose, max_len=CryptoService._PAYLOAD_MAX_LEN)
        if purpose_scope is not None:
            if len(purpose_scope) > CryptoService._PAYLOAD_SCOPE_MAX_ITEMS:
                errors.append({"field": "purpose_scope",
                               "reason": f"more than {CryptoService._PAYLOAD_SCOPE_MAX_ITEMS} elements"})
            for idx, item in enumerate(purpose_scope):
                for issue in v("purpose_scope", item,
                               max_len=CryptoService._PAYLOAD_MAX_LEN, forbidden="|,"):
                    errors.append({**issue, "index": idx})
        if errors:
            raise CDSError(
                "合约签名原文不得含 `|` / `,` 分隔符，且不得超过落库列宽："
                "有歧义的原文可被对方重新解释，平台拒签",
                code="VALIDATION_ERROR", http_status=422,
                detail={"errors": errors},
            )

        base = f"CDS-SIGN|{contract_id}|{contract_no}|{party_role}|{timestamp}"
        if purpose:
            base += f"|purpose:{purpose}"
        if purpose_scope:
            base += f"|purpose_scope:{','.join(str(s) for s in purpose_scope)}"
        return base.encode("utf-8")

    def sign_with_hsm(self, data: bytes, key_id: str) -> SM2Signature:
        """Sign data using HSM-managed key (P0-2).

        One attempt only. The private key behind ``key_id`` lives solely inside the
        adapter that holds it, so repeating the identical call cannot possibly succeed:
        the old body logged "falling back to software SM2" and then re-issued the same
        ``hsm_adapter.sign(data, key_id)`` (OCR-A113) — a fallback that never existed.
        A software keypair is the *caller's* explicit choice (audit_service and
        contract_service catch this and generate one under ``__software__``), which
        keeps the key source visible instead of pretending the HSM signed.

        Args:
            data: Raw bytes to sign
            key_id: HSM key identifier

        Returns:
            SM2Signature with hex-encoded r||s

        Raises:
            ValueError: the configured adapter cannot sign with this key_id — there
                is no second path for an HSM-held key id
        """
        from app.services.hsm_adapter import hsm_adapter
        try:
            sig_bytes = hsm_adapter.sign(data, key_id)
        except (RuntimeError, ValueError, AttributeError) as e:
            raise ValueError(
                f"Signing key {key_id} is unusable in the configured HSM adapter ({e}) — "
                f"no software fallback exists for an HSM-held key id"
            ) from e
        return SM2Signature(signature=sig_bytes.hex())

    def generate_hsm_signing_keypair(self, key_id: str) -> tuple[str, str]:
        """Generate an SM2 signing key pair in HSM (P0-2).

        Returns (public_key_hex, key_id). The private key never leaves HSM.

        Fails loudly when the adapter has no SM2 support. The old body logged
        "falling back to software SM2 keypair" and then re-raised the same exception
        (OCR-A113) — the log promised a fallback the code did not take. Generating a
        software keypair is the caller's visible decision, not a side effect.
        """
        from app.services.hsm_adapter import hsm_adapter
        return hsm_adapter.generate_signing_keypair(key_id)


crypto_service = CryptoService()
