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


# ─── SM2 密文定宽框架与 gmssl 底层工具（docs/cr-2026-10-15-review.md §4.12）──────
# 本机实测锚（.venv gmssl 3.2.2 纯 Python 版，2026-10-30 探针）：
#  1) CryptSM2.encrypt / CryptSM2.decrypt 的**第一行都是 `data.hex()`** —— 输入硬性要求
#     **bytes**；喂 hex str 必炸 `AttributeError: 'str' object has no attribute 'hex'`
#     （旧注释 "gmssl expects hex string input" 方向正好写反，是远程 300/300 解密失败的根因）。
#  2) 密文总宽 = C1(64B，无 04 前缀) + C3(32B) + C2(len(明文))；gmssl.decrypt 按固定偏移
#     `data[0:128hex]` 切 C1，而 C1/C2 裸 hex 一旦丢前导零宽度就掉到 96+n 以下（远程 py3.12
#     镜像曾记录 8 字节明文出 103/104/105 变长，§4.12），固定偏移随即错位。本机 3.2.2 已用
#     '%064x%064x' 定宽填充（300/300 恒 104），但 decrypt 只计算 C3、**不比对**——实测篡改
#     C2 区返回静默垃圾而非报错。因此：加密侧从严——自己校验宽度，不合格就换随机数重试
#     （gmssl 每次 encrypt 内部重取随机 k，重试即换 k），绝不出变长密文；解密侧宽容——
#     bytes/hex str、04 前缀都收，接受前自行重算 C3 验证，其余全部清晰抛错。
#  3) 构造函数 `public_key.lstrip("04")` 按字符集 {0,4} 删前导字符而非删前缀：x 坐标以
#     0/4 开头的公钥会被过度剥离（实测 128→125），随后 _kg 崩 TypeError 或验签 False——
#     全量跑「gmssl SM2 稀有单发」（test_result_verifier::test_valid_signature、
#     test_p0_8_9_10 DEK 用例，每密钥对约 1/256）的另一来源。一律空公钥构造后覆写归一值。
_SM2_COORD_HEX = 64        # 单坐标宽度（hex 字符）
_SM2_C1_HEX = 128          # C1 = x||y，无 04 前缀
_SM2_C3_HEX = 64           # C3 = SM3 摘要
_SM2_ENCRYPT_MAX_ATTEMPTS = 64  # 换随机数重试上限；丢前导零每坐标约 1/256，落空概率 ≈ (1/256)^64


def _sm2_unavailable(op: str) -> RuntimeError:
    """gmssl 缺失时的 SM2 fail-closed 报错（批 A：只响亮拒绝，绝不静默降级）。

    文案给出修复路径并钉死开关口径：SM2 面没有任何降级开关，
    CDS_SM3_FALLBACK_ALLOWED 只管 sm3_hash() 的 SHA-256 降级披露（PZ-04/MR-A14），
    永远不覆盖 SM2 sign/verify/encrypt/decrypt。
    """
    return RuntimeError(
        f"SM2 operation '{op}' is unavailable: the gmssl library is not installed. "
        "Fix: pip install 'gmssl>=3.2.2' (requirements.txt already pins it). SM2 has NO "
        "software fallback and no switch can enable one — CDS_SM3_FALLBACK_ALLOWED only "
        "gates the disclosed SHA-256 fallback of sm3_hash() (PZ-04/MR-A14); it deliberately "
        "never covers SM2 sign/verify/encrypt/decrypt (国密诚实性: refuse loudly, never fake)."
    )


def _sm2_normalize_public_key(public_key: str) -> str:
    """公钥归一：接受 128 hex（裸 x||y）或 130 hex（04 前缀），统一为小写裸 128。"""
    pub = public_key.strip().lower()
    if len(pub) == _SM2_C1_HEX + 2 and pub.startswith("04"):
        pub = pub[2:]  # 去且仅去一次 04 前缀 —— 不交给 lstrip("04")
    if len(pub) != _SM2_C1_HEX or any(c not in "0123456789abcdef" for c in pub):
        raise ValueError(
            "Invalid SM2 public key: expected 128 hex chars (bare x||y) or 130 with a 04 "
            f"prefix, got a {len(public_key)}-char value"
        )
    return pub


def _sm2_new_crypt(private_key: str, public_key: str):
    """构造 CryptSM2 并绕开构造函数 `lstrip("04")` 的公钥过度剥离（见上方实测锚 3）。"""
    crypt = _sm2.CryptSM2(private_key=private_key or "", public_key="")
    if public_key:
        crypt.public_key = _sm2_normalize_public_key(public_key)
    return crypt


class CryptoService:
    """SM2/SM3 cryptographic operations for contract signing and verification."""

    def generate_keypair(self) -> SM2KeyPair:
        """Generate an SM2 key pair using the SM2 curve parameters."""
        if not _HAS_GMSSL:
            raise _sm2_unavailable("generate_keypair")

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
            raise _sm2_unavailable("sign")

        import os
        public_key = _sm2_normalize_public_key(public_key)
        signer = _sm2_new_crypt(private_key, public_key)
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
            raise _sm2_unavailable("verify")

        public_key = _sm2_normalize_public_key(public_key)
        verifier = _sm2_new_crypt("", public_key)
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

        定宽契约（§4.12，加密侧从严）：返回值总宽**恒等于** ``96 + len(plaintext)`` 字节
        —— C1 64B（两坐标各 32 字节零填充，无 04 前缀）+ C3 32B + C2 len(plaintext)B。
        实现选择「校验宽度、不合格就换随机数重试」：gmssl 每次 encrypt 内部重取随机 k，
        本机 3.2.2 本就定宽（300/300 恒宽，重试为纯安全网），而对曾出 103/104/105 变长的
        库版本，重试直到两坐标满宽为止（每坐标落前导零约 1/256，64 次仍不合格的概率可忽略），
        杜绝同长明文出变长密文——变长密文会被 decrypt 的固定偏移切片静默错位。

        C3 检查段的排列次序跟随所装 gmssl 的 mode（3.2.x 默认 mode=0 即 C1||C2||C3，更早
        构建为 C1||C3||C2）；sm2_decrypt 依同一 mode 自适应解析，故本函数不再按旧 docstring
        谎称固定 C1||C3||C2。

        Args:
            plaintext: 非空 bytes
            public_key: 128 hex（裸 x||y），亦接受 130 hex（04 前缀）

        Raises:
            RuntimeError: gmssl 缺失，或 64 次重试仍不能产出定宽密文（响亮拒绝，不出半成品）
            ValueError: 空明文 / 公钥不合法
        """
        if not _HAS_GMSSL:
            raise _sm2_unavailable("sm2_encrypt")
        if not isinstance(plaintext, (bytes, bytearray)):
            raise ValueError(f"SM2 plaintext must be bytes, got {type(plaintext).__name__}")
        if not plaintext:
            raise ValueError("SM2 refuses an empty plaintext: C2 width 0 cannot be parsed back by any decrypt side")

        crypt = _sm2_new_crypt("", public_key)
        expected = (_SM2_C1_HEX + _SM2_C3_HEX) // 2 + len(plaintext)
        last_reason = "no attempt ran"
        for _ in range(_SM2_ENCRYPT_MAX_ATTEMPTS):
            try:
                encrypted = crypt.encrypt(bytes(plaintext))  # 实测：入参必须是 bytes
            except (TypeError, ValueError, AttributeError) as exc:
                last_reason = f"gmssl.encrypt raised {type(exc).__name__}: {exc}"
                continue
            if encrypted is None:
                last_reason = "gmssl.encrypt returned None (KDF degenerate branch, t == 0)"
                continue
            if isinstance(encrypted, str):
                # 某些构建返回裸 hex 串；奇数长度/非 hex 即宽度失守的信号 → fromhex 失败计入重试
                try:
                    encrypted = bytes.fromhex(encrypted)
                except ValueError as exc:
                    last_reason = f"gmssl.encrypt returned unparsable hex ({exc})"
                    continue
            if len(encrypted) == expected:
                return encrypted
            last_reason = (f"variable-width ciphertext: {len(encrypted)} bytes != fixed {expected} "
                           f"(unpadded C1/C2 coordinate — §4.12 defect signature)")
        raise RuntimeError(
            f"SM2 encryption failed to produce fixed-width ciphertext after "
            f"{_SM2_ENCRYPT_MAX_ATTEMPTS} fresh-random attempts — last: {last_reason}. "
            f"Refusing to emit a variable-width blob: the decrypt side slices C1 at a fixed "
            f"offset and would silently mis-decode it (CR 2026-10-15 §4.12)."
        )

    def sm2_decrypt(self, ciphertext, private_key: str, public_key: str) -> bytes:
        """Decrypt SM2 ciphertext with private key (GM/T 0003).

        解密侧宽容、验收从严（§4.12 兼容口径）：
        - 输入收 bytes 或 hex str（KMS ``sm2_encrypted_key`` 结果字段是 hex str，DB 列
          ``sm2_encrypted_payload`` 与 ``export_sm2_ciphertext()`` 是 bytes，两种都是真实入口）；
        - 容忍 C1 带 04 前缀的密文（作为第二候选解析，由 C3 验证裁决）；
        - gmssl decrypt 返回 None（KDF 退化分支）或内部崩溃 → 显式抛清晰错误，绝不返回 None；
        - **自行重算 C3 比对**后才放行（实测 gmssl.decrypt 只算不比对，篡改密文会静默返回
          垃圾明文）；
        - 历史变长密文（旧库丢坐标前导零的 103/105 字节族）无法可靠恢复切分，C3 必然失配
          → 清晰抛错而不是吐垃圾。数据无损结论的依据：该密文是**只写不读**通道 ——
          ``kms_service._sm2_decrypt`` 在 app/ 内零调用者，恢复器 ``kms_recovery`` 只把
          sm2_encrypted_payload 原样塞回内存字典供 KEK 通道使用，全仓没有任何解密消费点
          （grep 钉死，见 §4.12 影响面核实），故历史包袱 ≈ 0。

        Returns:
            通过 C3 验证的明文字节

        Raises:
            RuntimeError: gmssl 缺失
            ValueError: 输入不合法，或所有宽容解析路径都不能通过 C3 验证
        """
        if not _HAS_GMSSL:
            raise _sm2_unavailable("sm2_decrypt")
        if isinstance(ciphertext, (bytes, bytearray)):
            ct_hex = bytes(ciphertext).hex()
        elif isinstance(ciphertext, str):
            ct_hex = ciphertext.strip().lower()
        else:
            raise ValueError(f"SM2 ciphertext must be bytes or hex str, got {type(ciphertext).__name__}")
        try:
            ct_bytes = bytes.fromhex(ct_hex)
        except ValueError as exc:
            raise ValueError(f"SM2 ciphertext is not even-length hex ({exc})") from exc
        pk = private_key.strip().lower()
        if len(pk) != _SM2_COORD_HEX or any(c not in "0123456789abcdef" for c in pk):
            raise ValueError(f"SM2 private key must be 64 hex chars, got {len(private_key)}")

        crypt = _sm2_new_crypt(pk, public_key)
        # 3.2.x 默认 mode=0（C2 在前）；更早无 mode 属性的构建为 C1||C3||C2
        mode = getattr(crypt, "mode", 1)

        candidates = [ct_hex]
        if ct_hex.startswith("04") and len(ct_hex) >= 2 + (_SM2_C1_HEX + _SM2_C3_HEX) // 2 * 2 + 2:
            candidates.append(ct_hex[2:])  # 宽容：04 前缀的 C1（如标准 wire/其他实现产出）

        reasons: list[str] = []
        min_len = (_SM2_C1_HEX + _SM2_C3_HEX) // 2 + 1  # C1+C3+至少 1B 明文
        if len(ct_bytes) < min_len:
            reasons.append(
                f"ciphertext is {len(ct_bytes)}B < minimum {min_len}B (C1 64B + C3 32B + C2 ≥1B)")
        for h in candidates:
            if len(h) < min_len * 2:
                continue
            try:
                decrypted = crypt.decrypt(ct_bytes if h is ct_hex else bytes.fromhex(h))  # 实测：decrypt 要求 bytes
            except (TypeError, ValueError, AttributeError, IndexError) as exc:
                reasons.append(f"gmssl.decrypt raised {type(exc).__name__}: {exc}")
                continue
            if decrypted is None:
                reasons.append("gmssl.decrypt returned None (KDF degenerate branch, t == 0)")
                continue
            plaintext = bytes.fromhex(decrypted) if isinstance(decrypted, str) else decrypted
            # C3 自验证：C3 = SM3(x2 || M || y2)，其中 x2||y2 = d·C1
            try:
                xy = crypt._kg(int(pk, 16), h[:_SM2_C1_HEX])
                if not xy or len(xy) != _SM2_C1_HEX:
                    raise ValueError("C1 is not a parseable curve point at the fixed offset")
                x2 = "%064x" % int(xy[:_SM2_COORD_HEX], 16)
                y2 = "%064x" % int(xy[_SM2_COORD_HEX:_SM2_C1_HEX], 16)
                expect_c3 = _sm3.sm3_hash(list(bytes.fromhex(x2 + plaintext.hex() + y2)))
            except (TypeError, ValueError, AttributeError) as exc:
                reasons.append(f"C3 recheck failed ({type(exc).__name__}: {exc})")
                continue
            got_c3 = h[_SM2_C1_HEX:_SM2_C1_HEX + _SM2_C3_HEX] if mode else h[-_SM2_C3_HEX:]
            if expect_c3 != got_c3:
                reasons.append(
                    "C3 mismatch — ciphertext was tampered/truncated, or carries a legacy "
                    "variable-width C1 that fixed-offset parsing cannot recover")
                continue
            return plaintext
        raise ValueError(
            "SM2 decryption failed for every tolerated parse ("
            + "; ".join(reasons or ["empty candidate set"])
            + "). Fixed-width blobs produced by this service decrypt cleanly; legacy "
            "variable-width blobs (the 103/104/105-byte family, CR 2026-10-15 §4.12) cannot "
            "be re-split unambiguously and are rejected loudly instead of returning garbage."
        )

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
