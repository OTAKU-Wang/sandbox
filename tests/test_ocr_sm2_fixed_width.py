"""SM2 DEK 通道定宽 + 往返回归针（docs/cr-2026-10-15-review.md §4.12 的机检红黑门）。

把「gmssl SM2 稀有单发」（ci/known-failures.md 分类表）从人工记票变成每次都跑的机检门：

- **定宽门**：同一明文 200 次加密，宽度必须恒等于 96+n 字节（C1 64B + C3 32B + C2 nB）。
  稀有单发的检出概率口径：本机 gmssl 3.2.2 实测已用 '%064x' 定宽（300/300 恒 104），变长
  证据（8B 明文 → 103/104/105）来自 §4.12 远程 py3.12 镜像探针记录；若回归到不定宽版本，
  每次加密两坐标各约 1/256 概率掉宽，本针 200 单发 + 200 往返（合计 400 次加密）的漏检
  概率 ≈ (1 − (1−(255/256)²))⁻ 口径下约 (127/128)⁴⁰⁰ ≈ 4.4%（对不定宽库），且宽度断言是
  恒等式断言——对定宽实现零误报。往返门再叠加每笔 C3 自验证，静默错位零漏检。
  耗时实测（本机 py3.14 + gmssl 3.2.2 探针）：~7.5 ms/op，400 加密 + 200 解密 ≈ 6-8 s，
  远低于 pytest-timeout 600 s，无需降次。
- **KMS DEK 通道端到端**：wrap 后 unwrap 必须拿回 DEK 原字节。
- **结构针**：gmssl 缺失时全部 SM2 入口必须抛错、绝不返回成功——本针**不依赖** gmssl，
  任何环境都真跑（fail-closed，批 A 口径：报错文案必须给出修复路径与开关归属）。

无 gmssl 环境的 skip 条件：模块级只依赖 crypto_service 的 `_HAS_GMSSL` 门做标记，EC 数学
类针 skip、fail-closed 结构针照跑；本机装了 gmssl 时 _HAS_GMSSL 为 True，所有针真跑。
"""
import inspect

import pytest

import app.services.crypto_service as cs_mod
from app.services.crypto_service import _HAS_GMSSL, crypto_service

needs_gmssl = pytest.mark.skipif(
    not _HAS_GMSSL,
    reason="gmssl 未安装 —— SM2 EC 数学不可用；fail-closed 结构针不受影响照常跑",
)

FIXED_OVERHEAD = 96  # C1 64B + C3 32B，无 04 前缀


# ─────────────────────────── 结构针：缺失即响亮拒绝（批 A）───────────────────────────


def test_missing_gmssl_sm2_surface_never_returns_success(monkeypatch):
    """gmssl 缺失时 SM2 面必须抛错，不得返回成功（结构性 fail-closed）。

    报错文案要求（批 A 落地①）：给出修复路径（装哪个包），并钉死开关归属——
    CDS_SM3_FALLBACK_ALLOWED 只管 SM3 的 SHA-256 降级披露，永远不覆盖 SM2。
    """
    entry_points = {
        "generate_keypair": lambda: crypto_service.generate_keypair(),
        "sign": lambda: crypto_service.sign(b"data", "00" * 32, "11" * 64),
        "verify": lambda: crypto_service.verify(b"data", "00" * 64, "11" * 64),
        "sm2_encrypt": lambda: crypto_service.sm2_encrypt(b"data", "11" * 64),
        "sm2_decrypt": lambda: crypto_service.sm2_decrypt(b"\x00" * 104, "00" * 32, "11" * 64),
    }
    for name, call in entry_points.items():
        # 结构面：入口函数体必须先过 _HAS_GMSSL 门（防未来重构悄悄拆门）
        src = inspect.getsource(getattr(crypto_service, name))
        assert "_HAS_GMSSL" in src, f"{name}: _HAS_GMSSL 门禁被移除"
        # 行为面：翻门后必须抛错，绝不返回
        monkeypatch.setattr(cs_mod, "_HAS_GMSSL", False)
        with pytest.raises(RuntimeError, match="gmssl") as excinfo:
            call()
        message = str(excinfo.value)
        assert "gmssl>=3.2.2" in message, f"{name}: 报错缺少修复路径"
        assert "CDS_SM3_FALLBACK_ALLOWED" in message, f"{name}: 报错缺少开关口径"


# ─────────────────────────────── EC 数学类（需 gmssl）───────────────────────────────


@pytest.fixture(scope="module")
def keypair():
    return crypto_service.generate_keypair()


@needs_gmssl
def test_encrypt_is_fixed_width_200x(keypair):
    """定宽门：同一 8 字节明文 200 次加密，宽度恒为 96+8，杜绝 103/104/105 变长族。"""
    plaintext = b"\x00\x11" + b"ab12"
    widths = set()
    for _ in range(200):
        blob = crypto_service.sm2_encrypt(plaintext, keypair.public_key)
        assert isinstance(blob, bytes)
        widths.add(len(blob))
    assert widths == {FIXED_OVERHEAD + len(plaintext)}, (
        f"同长明文出了变长密文 {sorted(widths)} —— §4.12 缺陷签名（C1/C2 裸 hex 丢前导零），"
        f"decrypt 的固定偏移切片会静默错位"
    )


@needs_gmssl
def test_encrypt_fixed_width_across_lengths(keypair):
    """定宽对任意明文长度成立（含 DEK 的 32B），不是只对 8B 的巧合。"""
    for size in (1, 8, 16, 32, 48):
        plaintext = bytes(range(size % 256)) * (size // 256 + 1)
        plaintext = plaintext[:size].ljust(size, b"\x00")
        widths = {len(crypto_service.sm2_encrypt(plaintext, keypair.public_key))
                  for _ in range(25)}
        assert widths == {FIXED_OVERHEAD + size}, f"{size}B 明文宽度漂移: {sorted(widths)}"


@needs_gmssl
def test_roundtrip_200x(keypair):
    """往返门：200 次加密→解密全部拿回原文（每笔都过解密侧 C3 自验证）。"""
    plaintext = b"dek-" + b"\x00" * 4 + b"sm2"  # 8B，含前导零诱饵
    for i in range(200):
        blob = crypto_service.sm2_encrypt(plaintext, keypair.public_key)
        assert len(blob) == FIXED_OVERHEAD + len(plaintext)
        assert crypto_service.sm2_decrypt(blob, keypair.private_key, keypair.public_key) == plaintext


@needs_gmssl
def test_kms_dek_channel_wrap_unwrap_e2e():
    """KMS DEK 通道端到端：generate_data_key wrap 后，unwrap 必须原样拿回 DEK。

    §4.12 的影响面结论「潜在而非线上故障」的依据同针钉死：这条 unwrap 路径在旧实现里
    是 300/300 AttributeError 的死路径；本针让它成为常驻机检门。
    """
    from app.services.kms_service import KMSService

    kp = crypto_service.generate_keypair()
    svc = KMSService()
    info = svc.generate_data_key("ocr-sm2-fixed-width", cert_public_key=kp.public_key)

    dek = info["key_bytes"]
    assert dek is not None and len(dek) == 32
    wrapped_hex = info["sm2_encrypted_key"]
    assert wrapped_hex is not None and wrapped_hex != dek.hex()
    assert len(bytes.fromhex(wrapped_hex)) == FIXED_OVERHEAD + 32, "DEK 密文必须定宽 128B"

    # unwrap（两条入口都钉：服务层 helper 与 crypto 层）
    wrapped = bytes.fromhex(wrapped_hex)
    assert svc._sm2_decrypt(wrapped, kp.private_key, kp.public_key) == dek
    assert crypto_service.sm2_decrypt(wrapped_hex, kp.private_key, kp.public_key) == dek
    # 持久化导出副本与内存一致（DB 列写的就是这份 bytes）
    assert svc.export_sm2_ciphertext(info["key_id"]) == wrapped
    # 错误的证书私钥拿不回 DEK，且是清晰错误而非垃圾/None
    other = crypto_service.generate_keypair()
    with pytest.raises(ValueError, match="SM2 decryption failed"):
        crypto_service.sm2_decrypt(wrapped, other.private_key, other.public_key)


@needs_gmssl
def test_decrypt_is_lenient_on_input_forms(keypair):
    """解密侧宽容：bytes / hex str / 大写 hex / 04 前缀密文 / 04 前缀公钥都收。"""
    plaintext = b"forms!"
    blob = crypto_service.sm2_encrypt(plaintext, keypair.public_key)
    assert crypto_service.sm2_decrypt(blob, keypair.private_key, keypair.public_key) == plaintext
    assert crypto_service.sm2_decrypt(blob.hex(), keypair.private_key, keypair.public_key) == plaintext
    assert crypto_service.sm2_decrypt(blob.hex().upper(), keypair.private_key, keypair.public_key) == plaintext
    assert crypto_service.sm2_decrypt(b"\x04" + blob, keypair.private_key, keypair.public_key) == plaintext
    assert crypto_service.sm2_decrypt(blob, keypair.private_key, "04" + keypair.public_key) == plaintext


@needs_gmssl
def test_decrypt_rejects_tampered_truncated_and_legacy_variable_width(keypair):
    """验收从严：篡改/截断/历史变长一律清晰抛错，绝不静默吐垃圾（gmssl 原生只算不比对 C3）。"""
    plaintext = b"reject!"
    blob = crypto_service.sm2_encrypt(plaintext, keypair.public_key)

    tampered = bytearray(blob)
    tampered[len(blob) // 2] ^= 0xFF  # C2 区（实测：旧实现在这里返回静默垃圾）
    with pytest.raises(ValueError, match="C3 mismatch"):
        crypto_service.sm2_decrypt(bytes(tampered), keypair.private_key, keypair.public_key)

    with pytest.raises(ValueError, match="SM2 decryption failed"):
        crypto_service.sm2_decrypt(blob[:-1], keypair.private_key, keypair.public_key)

    # 历史变长族（§4.12 的 103 字节形态）：无法可靠恢复切分 → 清晰拒绝
    legacy_variable_width = blob[:60] + blob[61:]
    with pytest.raises(ValueError, match="SM2 decryption failed"):
        crypto_service.sm2_decrypt(legacy_variable_width, keypair.private_key, keypair.public_key)

    with pytest.raises(ValueError, match="even-length hex"):
        crypto_service.sm2_decrypt("abc", keypair.private_key, keypair.public_key)
    with pytest.raises(ValueError, match="minimum"):
        crypto_service.sm2_decrypt(b"\x00" * 8, keypair.private_key, keypair.public_key)


@needs_gmssl
def test_rare_flake_gate_pubkey_x_starting_with_04():
    """稀有单发机检门：x 坐标以 04 开头的公钥（旧构造器 lstrip("04") 过度剥离，~1/256/密钥对）。

    旧实现下本用例是必红针（encrypt 崩 TypeError / verify False 的稀有单发族，解释
    test_result_verifier::test_valid_signature 与 test_p0_8_9_10 DEK 用例的全量单发）；
    现在公钥先归一再覆写，本用例必绿。私钥标量 11 是本机上确定命中 04 前缀的最小值。
    """
    from gmssl import sm2 as g

    priv = "%064x" % 11
    pub = g.CryptSM2(private_key=priv, public_key="")._kg(11, g.default_ecc_table["g"])
    assert pub.startswith("04"), "夹具失效：该私钥的公钥不再以 04 开头（换夹具标量）"

    plaintext = b"lstrip!"
    assert crypto_service.sm2_decrypt(
        crypto_service.sm2_encrypt(plaintext, pub), priv, pub
    ) == plaintext
    sig = crypto_service.sign(b"lstrip!", priv, pub)
    assert crypto_service.verify(b"lstrip!", sig.signature, pub) is True


@needs_gmssl
def test_invalid_inputs_raise_clear_errors(keypair):
    """入口校验：空明文/坏公钥/坏私钥都是清晰 ValueError，不穿透成 NoneType/TypeError。"""
    with pytest.raises(ValueError, match="empty plaintext"):
        crypto_service.sm2_encrypt(b"", keypair.public_key)
    with pytest.raises(ValueError, match="public key"):
        crypto_service.sm2_encrypt(b"ok", "abcd")
    with pytest.raises(ValueError, match="private key"):
        crypto_service.sm2_decrypt(b"\x00" * 104, "abcd", keypair.public_key)
    with pytest.raises(ValueError):
        crypto_service.sm2_encrypt(b"ok", b"not a hex string")  # type: ignore[arg-type]
