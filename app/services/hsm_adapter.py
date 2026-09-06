"""HSM Adapter — Hardware Security Module interface for key management.

Provides:
1. Abstract HSM interface
2. Vault Transit implementation
3. Software fallback (dev/test)

In production, HSM stores the Key Encryption Key (KEK) that wraps DEKs.
DEKs are never stored in plaintext outside TEE.
"""
import logging
import os
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class HSMAdapter(ABC):
    """Abstract HSM interface for key operations."""

    @abstractmethod
    def generate_kek(self, key_id: str) -> bytes:
        """Generate a Key Encryption Key (KEK) in HSM."""
        ...

    @abstractmethod
    def wrap_dek(self, kek_id: str, dek_bytes: bytes) -> bytes:
        """Wrap a DEK with KEK (encrypt DEK for storage)."""
        ...

    @abstractmethod
    def unwrap_dek(self, kek_id: str, wrapped_dek: bytes) -> bytes:
        """Unwrap a DEK using KEK (decrypt DEK for use in TEE)."""
        ...

    @abstractmethod
    def rotate_kek(self, key_id: str) -> str:
        """Rotate a KEK. Returns new key_id."""
        ...

    @abstractmethod
    def sign(self, data: bytes, key_id: str) -> bytes:
        """Sign data using HSM-managed key. Returns raw signature bytes.

        For SM2, returns 64-byte r||s signature.
        Raises RuntimeError if HSM cannot sign (e.g. unsupported algorithm).
        """
        ...

    @abstractmethod
    def generate_signing_keypair(self, key_id: str) -> tuple[str, str]:
        """Generate a signing key pair in HSM. Returns (public_key, key_ref).

        For SM2: public_key is 128-char hex (uncompressed point).
        key_ref is the HSM key identifier for subsequent sign() calls.
        """
        ...


class VaultTransitAdapter(HSMAdapter):
    """HashiCorp Vault Transit secrets engine adapter.

    Production implementation — uses Vault for KEK management.
    """

    def __init__(self, vault_addr: str = "", vault_token: str = ""):
        self._vault_addr = vault_addr
        self._vault_token = vault_token
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import hvac
                self._client = hvac.Client(url=self._vault_addr, token=self._vault_token)
                if not self._client.is_authenticated():
                    logger.warning("Vault authentication failed — falling back to software HSM")
                    self._client = None
            except ImportError:
                logger.warning("hvac not installed — Vault Transit unavailable")
            except Exception as e:
                logger.warning(f"Vault connection failed: {e}")
        return self._client

    def generate_kek(self, key_id: str) -> bytes:
        client = self._get_client()
        if client:
            try:
                client.secrets.transit.create_key(name=key_id, type="aes256-gcm96")
                # KEK stays in HSM — return marker
                return f"hsm:{key_id}".encode()
            except Exception as e:
                logger.warning(f"Vault KEK generation failed: {e}")
        raise RuntimeError("HSM unavailable — cannot generate KEK")

    def wrap_dek(self, kek_id: str, dek_bytes: bytes) -> bytes:
        client = self._get_client()
        if client:
            try:
                response = client.secrets.transit.encrypt_data(name=kek_id, plaintext=dek_bytes.hex())
                return bytes.fromhex(response["data"]["ciphertext"])
            except Exception as e:
                logger.warning(f"Vault wrap failed: {e}")
        raise RuntimeError("HSM unavailable — cannot wrap DEK")

    def unwrap_dek(self, kek_id: str, wrapped_dek: bytes) -> bytes:
        client = self._get_client()
        if client:
            try:
                response = client.secrets.transit.decrypt_data(name=kek_id, ciphertext=wrapped_dek.hex())
                return bytes.fromhex(response["data"]["plaintext"])
            except Exception as e:
                logger.warning(f"Vault unwrap failed: {e}")
        raise RuntimeError("HSM unavailable — cannot unwrap DEK")

    def rotate_kek(self, key_id: str) -> str:
        client = self._get_client()
        if client:
            try:
                client.secrets.transit.rotate_key(name=key_id)
                return key_id  # Same key_id, new version in Vault
            except Exception as e:
                logger.warning(f"Vault rotate failed: {e}")
        raise RuntimeError("HSM unavailable — cannot rotate KEK")

    def sign(self, data: bytes, key_id: str) -> bytes:
        """Sign data with Vault Transit. SM2 not natively supported — raises RuntimeError."""
        client = self._get_client()
        if client:
            try:
                # Vault Transit supports RSA/ECDSA but not SM2
                response = client.secrets.transit.sign_data(
                    name=key_id, input=data.hex(), hash_algorithm="sha2-256",
                )
                sig_hex = response["data"]["signature"]
                # Vault returns "vault:v1:<base64>" format
                import base64
                sig_b64 = sig_hex.split(":")[-1]
                return base64.b64decode(sig_b64)
            except Exception as e:
                logger.warning(f"Vault sign failed: {e}")
        raise RuntimeError("HSM unavailable for signing — use software fallback")

    def generate_signing_keypair(self, key_id: str) -> tuple[str, str]:
        """Vault Transit does not support SM2 key generation."""
        raise RuntimeError("Vault Transit does not support SM2 signing keys — use software fallback")


class SoftwareHSMAdapter(HSMAdapter):
    """Software fallback HSM for development/testing.

    Stores keys in memory. NOT for production use.
    """

    def __init__(self):
        self._keys: dict[str, bytes] = {}
        self._signing_keys: dict[str, tuple[str, str]] = {}  # key_id → (private_key, public_key)

    def generate_kek(self, key_id: str) -> bytes:
        kek = os.urandom(32)
        self._keys[key_id] = kek
        logger.info(f"Software HSM: generated KEK {key_id}")
        return kek

    def wrap_dek(self, kek_id: str, dek_bytes: bytes) -> bytes:
        kek = self._keys.get(kek_id)
        if not kek:
            raise ValueError(f"KEK {kek_id} not found")
        from app.utils.crypto import SM4Cipher
        cipher = SM4Cipher(kek)
        ciphertext, nonce, tag = cipher.encrypt_gcm(dek_bytes)
        return nonce + tag + ciphertext

    def unwrap_dek(self, kek_id: str, wrapped_dek: bytes) -> bytes:
        kek = self._keys.get(kek_id)
        if not kek:
            raise ValueError(f"KEK {kek_id} not found")
        nonce, tag, ct = wrapped_dek[:12], wrapped_dek[12:28], wrapped_dek[28:]
        from app.utils.crypto import SM4Cipher
        cipher = SM4Cipher(kek)
        return cipher.decrypt_gcm(ct, nonce, tag)

    def rotate_kek(self, key_id: str) -> str:
        new_key_id = f"{key_id}-v{len(self._keys) + 1}"
        self._keys[new_key_id] = os.urandom(32)
        logger.info(f"Software HSM: rotated KEK {key_id} → {new_key_id}")
        return new_key_id

    def sign(self, data: bytes, key_id: str) -> bytes:
        """Sign data with SM2 using software-managed key pair."""
        keys = self._signing_keys.get(key_id)
        if not keys:
            raise ValueError(f"Signing key {key_id} not found in software HSM")
        from app.services.crypto_service import crypto_service
        private_key, public_key = keys
        signature = crypto_service.sign(data, private_key, public_key)
        return bytes.fromhex(signature.signature)

    def generate_signing_keypair(self, key_id: str) -> tuple[str, str]:
        """Generate SM2 key pair in software HSM. Returns (public_key_hex, key_id)."""
        from app.services.crypto_service import crypto_service
        kp = crypto_service.generate_keypair()
        self._signing_keys[key_id] = (kp.private_key, kp.public_key)
        logger.info(f"Software HSM: generated SM2 signing keypair {key_id}")
        return kp.public_key, key_id


def create_hsm_adapter() -> HSMAdapter:
    """Create HSM adapter based on configuration.

    Tries Vault Transit first; falls back to software HSM only when
    explicitly allowed (gap B5/T12 — HSM_SOFTWARE_FALLBACK_ALLOWED=false
    makes the degradation fail-closed instead of silent).
    """
    vault_available = False
    try:
        from app.core.config import get_settings
        settings = get_settings()
        if hasattr(settings, 'VAULT_ADDR') and settings.VAULT_ADDR:
            adapter = VaultTransitAdapter(settings.VAULT_ADDR, settings.VAULT_TOKEN)
            # Probe Vault availability before committing
            if adapter._get_client() is not None:
                return adapter
            logger.info("Vault configured but unreachable")
    except Exception:
        pass

    fallback_allowed = True
    try:
        from app.core.config import get_settings as _gs
        fallback_allowed = bool(_gs().HSM_SOFTWARE_FALLBACK_ALLOWED)
    except Exception:
        pass
    if not fallback_allowed:
        raise RuntimeError(
            "HSM/Vault unavailable and HSM_SOFTWARE_FALLBACK_ALLOWED=false — "
            "refusing to fall back to the software HSM"
        )
    logger.info("Using software HSM fallback (dev/test only)")
    return SoftwareHSMAdapter()


# Singleton
hsm_adapter = create_hsm_adapter()
