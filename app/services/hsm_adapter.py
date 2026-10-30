"""HSM Adapter — Hardware Security Module interface for key management.

Provides:
1. Abstract HSM interface
2. Vault Transit implementation
3. Software fallback (dev/test)

In production, HSM stores the Key Encryption Key (KEK) that wraps DEKs.
DEKs are never stored in plaintext outside TEE.
"""
import base64
import logging
import os
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class HSMAdapter(ABC):
    """Abstract HSM interface for key operations.

    A94 (honest degradation / T12): every adapter declares where its keys
    actually live. `is_simulation` is True for any adapter with NO hardware
    backing, and `backend` is a machine-readable name for boot disclosure.
    Without these two markers a caller (kms_service today) cannot tell a
    software-KEK process from a real HSM, which is exactly the discrimination
    the honest-degradation rule requires.
    """

    #: True when keys live in this process rather than in hardware/Vault.
    is_simulation: bool = False
    #: Machine-readable backend id ("vault-transit" | "software-fallback").
    backend: str = "unknown"

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

    is_simulation = False
    backend = "vault-transit"

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

    def transit_available(self) -> bool:
        """Vault 已认证还不够 — transit 引擎未挂载时 KEK 操作必失败。

        2026-10-16 实测：一台 Vault 可达且已认证、但没挂 transit 的机器上，本工厂
        依然选中 Vault 适配器，之后每次 KEK 都死在
        `no handler for route "transit/keys/cds-master-kek"`，而日志里那句
        “falling back to software HSM” 从来不会发生 — 结果推送注册、卷 DEK 登记
        全部 500。诚实降级的前提是降级路径真的可达。
        """
        client = self._get_client()
        if client is None:
            return False
        try:
            mounts = client.sys.list_mounted_secrets_engines() or {}
        except Exception as e:
            logger.warning(f"Vault mount probe failed: {e}")
            return False
        return any(str(path).startswith("transit") for path in mounts)

    def generate_kek(self, key_id: str) -> bytes:
        client = self._get_client()
        if client:
            try:
                # D-e2e-12: hvac's create_key takes key_type= (not type=) —
                # the wrong kwarg raised TypeError on a REAL Vault and every
                # KEK op 500'd; local suites use the software HSM and never
                # reached this path (2871-green structural blind spot).
                client.secrets.transit.create_key(name=key_id, key_type="aes256-gcm96")
                # KEK stays in HSM — return marker
                return f"hsm:{key_id}".encode()
            except Exception as e:
                logger.warning(f"Vault KEK generation failed: {e}")
        raise RuntimeError("HSM unavailable — cannot generate KEK")

    def wrap_dek(self, kek_id: str, dek_bytes: bytes) -> bytes:
        client = self._get_client()
        if client:
            try:
                # D-e2e-13: Vault transit's REST contract is BASE64 in both
                # directions (hvac passes plaintext through, no client-side
                # conversion). The old code sent hex plaintext and ran the
                # returned "vault:v1:<base64>" ciphertext through
                # bytes.fromhex — guaranteed to fail on a real Vault (local
                # suites use the software adapter; structural blind spot).
                response = client.secrets.transit.encrypt_data(
                    name=kek_id,
                    plaintext=base64.b64encode(dek_bytes).decode(),
                )
                # Store the versioned ciphertext verbatim ("vault:v1:<b64>")
                # — it is self-describing across KEK rotations.
                return response["data"]["ciphertext"].encode()
            except Exception as e:
                logger.warning(f"Vault wrap failed: {e}")
        raise RuntimeError("HSM unavailable — cannot wrap DEK")

    def unwrap_dek(self, kek_id: str, wrapped_dek: bytes) -> bytes:
        client = self._get_client()
        if client:
            try:
                ciphertext = wrapped_dek.decode() if isinstance(wrapped_dek, (bytes, bytearray)) else wrapped_dek
                response = client.secrets.transit.decrypt_data(
                    name=kek_id, ciphertext=ciphertext,
                )
                return base64.b64decode(response["data"]["plaintext"])
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
                # (D-e2e-13: sign input is base64 in the transit REST contract)
                response = client.secrets.transit.sign_data(
                    name=key_id, input=base64.b64encode(data).decode(),
                    hash_algorithm="sha2-256",
                )
                sig_hex = response["data"]["signature"]
                # Vault returns "vault:v1:<base64>" format. The module-level
                # `base64` import is the only one: a second `import base64`
                # here made it a FUNCTION-LOCAL name, so the `base64.b64encode`
                # a few lines above always raised UnboundLocalError and the
                # real Vault signing path could never succeed (A94c).
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

    A94: the fallback must be discernible from the outside — `is_simulation`
    and `backend` are the markers the boot disclosure (T12) and any caller
    read to know the KEK is a process-local random value, not hardware.

    P2-3 (todo-plan 2026-10-30, ss-19 §10.1-2): optional durable master key.
    With `master_key_file` set, a 32-byte master secret is loaded from (or
    0600-generated into) that file at construction, and every KEK is derived
    deterministically as HMAC-SHA256(master, "cds-kms-kek-v1:" + key_id).
    Same file ⇒ same KEK after a container restart, so envelopes wrapped
    before the restart unwrap again — the actual root cause behind the
    honest-but-fatal "密钥…在 KMS 中不存在" errors (D-e2e-14). Without the
    setting the behavior is byte-identical to the old in-memory adapter.
    This is durability, NOT hardware: `is_simulation` stays True and the
    boot disclosure keeps saying 非 HSM (真 KMS/HSM 属 P5 硬件轨道，禁伪造).
    SM2 signing key pairs stay in-memory — they are session-scoped ceremony,
    disclosed alongside the backend, and out of P2-3 scope.
    """

    is_simulation = True
    backend = "software-fallback"

    _KEK_DERIVATION_PREFIX = b"cds-kms-kek-v1:"

    def __init__(self, master_key_file: str = ""):
        self._keys: dict[str, bytes] = {}
        self._signing_keys: dict[str, tuple[str, str]] = {}  # key_id → (private_key, public_key)
        self.master_key_file = (master_key_file or "").strip()
        self._master_key: bytes | None = None
        #: True exactly when this construction generated the durable file for
        #: the first time — main.py's lifespan turns it into the one-time
        #: `kms.master_key.generated` audit event (import time has no DB).
        self.master_key_just_generated = False
        if self.master_key_file:
            self._master_key = self._load_or_create_master_key()

    # -- P2-3 durable master key ------------------------------------------
    def _load_or_create_master_key(self) -> bytes:
        import hashlib
        import stat

        path = self.master_key_file
        if os.path.exists(path):
            mode = stat.S_IMODE(os.stat(path).st_mode)
            if mode & 0o077:
                raise RuntimeError(
                    f"KMS 主密钥文件权限过宽（mode={oct(mode)}）：{path} 必须 0600，"
                    "否则任何本地用户都能读出主密钥。拒绝加载。"
                )
            with open(path, "rb") as f:
                material = f.read()
            if len(material) != 32:
                raise RuntimeError(
                    f"KMS 主密钥文件损坏（{len(material)} 字节，期望 32）：{path}。"
                    "拒绝用一把读不通的主密钥启动——宁可拒启，不要悄悄生成新的让旧信封全灭。"
                )
            logger.info("[KMS] 软件主密钥已从文件加载（非 HSM）：%s", path)
            return material
        parent = os.path.dirname(os.path.abspath(path))
        if not os.path.isdir(parent) or not os.access(parent, os.W_OK):
            raise RuntimeError(
                f"KMS 主密钥文件路径不可用：{path}（父目录缺失或不可写——挂载卷没挂上？）。"
                "拒绝退回到内存态：那会让这次生成的 KEK 随进程消失，旧信封从此不可解。"
            )
        material = os.urandom(32)
        tmp = f"{path}.tmp-{os.getpid()}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(material)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        mode = stat.S_IMODE(os.stat(path).st_mode)
        if mode & 0o077:
            os.chmod(path, 0o600)
        self.master_key_just_generated = True
        logger.warning(
            "[KMS] 首次启动生成软件主密钥文件（非 HSM，0600）：%s（sha256=%s…）—— "
            "备份/迁移该文件即迁移全部既有信封的可解密性；删除它等于销毁全部数据。",
            path, hashlib.sha256(material).hexdigest()[:12],
        )
        return material

    def _derive_kek(self, key_id: str) -> bytes:
        import hashlib
        import hmac

        assert self._master_key is not None
        return hmac.new(
            self._master_key, self._KEK_DERIVATION_PREFIX + key_id.encode("utf-8"),
            hashlib.sha256,
        ).digest()

    @property
    def master_key_persistent(self) -> bool:
        return self._master_key is not None

    def generate_kek(self, key_id: str) -> bytes:
        if self._master_key is not None:
            # 确定性派生：同主密钥 + 同 key_id ⇒ 跨重启恒得同一把 KEK，
            # 旧信封在新进程里照常 unwrap（P2-3 的关单判据）。
            kek = self._derive_kek(key_id)
            self._keys[key_id] = kek
            logger.info(f"Software HSM: derived KEK {key_id} from durable master key file")
            return kek
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
        if self._master_key is not None:
            # 派生模式下新 KEK 同样随主密钥文件持久（P2-3 口径一致）。命名沿用
            # len+1 旧形——本仓无 rotate 生产调用方，软件后端真轮转属 P5 硬件轨道。
            self._keys[new_key_id] = self._derive_kek(new_key_id)
        else:
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
                if adapter.transit_available():
                    return adapter
                logger.warning(
                    "Vault reachable but the transit engine is not mounted — "
                    "treating the HSM as unavailable"
                )
            else:
                logger.info("Vault configured but unreachable")
    except Exception as e:
        logger.warning(
            f"Vault HSM probe raised ({e.__class__.__name__}: {e}) — evaluating software fallback"
        )

    fallback_allowed = False
    try:
        from app.core.config import get_settings as _gs
        fallback_allowed = bool(_gs().HSM_SOFTWARE_FALLBACK_ALLOWED)
    except Exception as e:
        logger.error(
            f"Cannot read HSM_SOFTWARE_FALLBACK_ALLOWED ({e.__class__.__name__}: {e}) — "
            "treating software fallback as disallowed (fail-closed)"
        )
    if not fallback_allowed:
        raise RuntimeError(
            "HSM/Vault unavailable and HSM_SOFTWARE_FALLBACK_ALLOWED=false — "
            "refusing to fall back to the software HSM"
        )
    master_key_file = ""
    try:
        master_key_file = str(getattr(_gs(), "KMS_MASTER_KEY_FILE", "") or "").strip()
    except Exception as e:
        logger.error(
            f"Cannot read KMS_MASTER_KEY_FILE ({e.__class__.__name__}: {e}) — "
            "software backend starts WITHOUT the durable master key (P2-3); "
            "既有信封在重启后将不可解密，配置修好前请勿投产"
        )
    if master_key_file:
        logger.warning(
            "HSM fallback in use: backend=software-fallback is_simulation=True — 软件主密钥文件"
            "（非 HSM）：%s，KEK 由它确定性派生、跨重启可解密既有信封；SM2 签名密钥对仍为"
            "内存态。真 KMS/HSM 属 P5 硬件轨道（A94/T12 honest disclosure）。",
            master_key_file,
        )
    else:
        logger.warning(
            "HSM fallback in use: backend=software-fallback is_simulation=True — the KEK "
            "lives in this process, NOT in an HSM (dev/test only; disable via "
            "HSM_SOFTWARE_FALLBACK_ALLOWED=false). Disclosed at WARNING so it survives "
            "production log levels (A94/T12). P2-3: 设 CDS_KMS_MASTER_KEY_FILE=<挂载卷路径>"
            "可让主密钥跨重启，否则容器重启后既有信封不可解密。"
        )
    return SoftwareHSMAdapter(master_key_file=master_key_file)


def describe_hsm_backend(adapter: HSMAdapter | None = None) -> dict[str, object]:
    """A94: machine-readable disclosure of the active HSM backend for boot (T12).

    Carries no key material — only which backend is live and whether it is a
    simulation. main.py's fallback disclosure is the consumer that still needs
    to read this (NEEDS-OWNER: app/main.py).
    """
    a = adapter if adapter is not None else hsm_adapter
    # 口径钉子（test_ocr_a094_hsm_disclosure 锁死本 dict 恰为两键，P2-3 的持久化
    # 事实走适配器属性 master_key_persistent/master_key_file 给 main.py 披露读，
    # 不在此 dict 里扩键）。
    return {
        "backend": getattr(a, "backend", "unknown"),
        "is_simulation": bool(getattr(a, "is_simulation", False)),
    }


# Singleton
hsm_adapter = create_hsm_adapter()
