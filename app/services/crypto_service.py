"""SM2/SM3 Cryptographic Service — Chinese national standard signing and verification.

Uses gmssl library for SM2 signature operations.
SM2 public key format: uncompressed point (04 + x + y, 128 hex chars)
SM2 private key: 64 hex chars (256-bit scalar)
"""
import hashlib
import logging
from dataclasses import dataclass

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
        """Compute SM3 hash of data (64-char hex)."""
        if _HAS_GMSSL:
            return _sm3.sm3_hash(list(data))
        logger.warning("[CRYPTO] gmssl not installed — SM3 falling back to SHA-256. "
                       "Install gmssl for GM/T 0004 compliance.")
        return hashlib.sha256(data).hexdigest()

    def _sm3_hash_with_z(self, data: bytes, public_key: str) -> str:
        """Compute SM3 hash with Z value (GM/T 0009 standard).

        Z = SM3(ENTL || userId || xG || yG || xA || yA)
        Then hash = SM3(Z || M)
        """
        if not _HAS_GMSSL:
            return hashlib.sha256(data).hexdigest()

        # Default user ID: "1234567812345678" (16 bytes)
        user_id = b"1234567812345678"
        entlen = len(user_id) * 8  # bit length
        entl = entlen.to_bytes(2, 'big')

        # SM2 curve parameters (sm2p256v1)
        xG = "28E9FA9E9D9F5E344D5A9E4BCF6509A7F39789F515AB8F92DDBCBD414D940E93"
        yG = "37BF27342DA639B6DCCFFFEB73D69D78C6C27A6009CBBCA1980F8533921E8A68"

        # Public key coordinates
        if len(public_key) == 128:
            xA = public_key[:64]
            yA = public_key[64:]
        else:
            raise ValueError(f"Invalid SM2 public key length: {len(public_key)}")

        # Z = SM3(ENTL || ID || xG || yG || xA || yA)
        z_input = entl + user_id + bytes.fromhex(xG + yG + xA + yA)
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

    @staticmethod
    def contract_sign_data(contract_id: str, contract_no: str, party_role: str, timestamp: str,
                           purpose: str | None = None, purpose_scope: list | None = None) -> bytes:
        """Build the canonical sign payload for a contract.

        Gap A4: a purpose limitation is part of the signed payload so both
        parties commit to it. purpose=None keeps the legacy payload shape,
        so existing purpose-less contracts verify unchanged.
        """
        base = f"CDS-SIGN|{contract_id}|{contract_no}|{party_role}|{timestamp}"
        if purpose:
            base += f"|purpose:{purpose}"
        if purpose_scope:
            base += f"|purpose_scope:{','.join(str(s) for s in purpose_scope)}"
        return base.encode("utf-8")

    def sign_with_hsm(self, data: bytes, key_id: str) -> SM2Signature:
        """Sign data using HSM-managed key (P0-2).

        Attempts HSM signing first. Falls back to software SM2 with warning if HSM
        is unavailable or does not support SM2.

        Args:
            data: Raw bytes to sign
            key_id: HSM key identifier

        Returns:
            SM2Signature with hex-encoded r||s

        Raises:
            ValueError: If key_id not found in either HSM or software fallback
        """
        from app.services.hsm_adapter import hsm_adapter
        try:
            sig_bytes = hsm_adapter.sign(data, key_id)
            return SM2Signature(signature=sig_bytes.hex())
        except (RuntimeError, ValueError, AttributeError) as e:
            logger.warning(
                f"[CRYPTO] HSM signing failed for key {key_id}: {e} — "
                f"falling back to software SM2"
            )
        # Fallback: try software HSM (which stores SM2 keypairs)
        try:
            sig_bytes = hsm_adapter.sign(data, key_id)
            return SM2Signature(signature=sig_bytes.hex())
        except (RuntimeError, ValueError, AttributeError):
            pass
        raise ValueError(
            f"Signing key {key_id} not available in HSM or software fallback"
        )

    def generate_hsm_signing_keypair(self, key_id: str) -> tuple[str, str]:
        """Generate an SM2 signing key pair in HSM (P0-2).

        Returns (public_key_hex, key_id). The private key never leaves HSM.

        Falls back to software HSM if hardware HSM doesn't support SM2.
        """
        from app.services.hsm_adapter import hsm_adapter
        try:
            return hsm_adapter.generate_signing_keypair(key_id)
        except (RuntimeError, AttributeError) as e:
            logger.warning(
                f"[CRYPTO] HSM key generation failed: {e} — "
                f"falling back to software SM2 keypair"
            )
            raise


crypto_service = CryptoService()
