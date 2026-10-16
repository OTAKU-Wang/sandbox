"""SM2 签名摘要必须与 GM/T 0003.2 的 Z 构成一致（跨实现互操作回归）。

钉住的缺陷：`_sm3_hash_with_z` 曾把 Z 算成 SM3(ENTL‖ID‖xG‖yG‖xA‖yA)，
漏掉曲线系数 a、b，且 xG/yG 用了错误常量 —— 结果是服务端只能验自己签的名，
任何标准实现（浏览器 sm-crypto、HSM、第三方验签工具）100% 验不过。
"""
import pytest

from app.services.crypto_service import _HAS_GMSSL
from app.services.crypto_service import crypto_service

pytestmark = pytest.mark.skipif(
    not _HAS_GMSSL, reason="gmssl 未安装时 SM2 摘要退化为 SHA-256"
)

UID = "1234567812345678"

CURVE_A = "FFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF00000000FFFFFFFFFFFFFFFC"
CURVE_B = "28E9FA9E9D9F5E344D5A9E4BCF6509A7F39789F515AB8F92DDBCBD414D940E93"
CURVE_GX = "32C4AE2C1F1981195F9904466A39C9948FE30BBFF2660BE1715A4589334C74C7"
CURVE_GY = "BC3736A2F4F6779C59BDCEE36B692153D0A9877CC62A474002DF32E52139F0A0"

INTEROP_PUB = (
    "6bdd37e29d2c0f8268a7047b23493b255ff29cf2f584a5b6605d681cc6302d57"
    "2dc343c7afd25c7504bc5d1cd885da1224f469737f91144a3bc59ed25333d3e9"
)
INTEROP_MSG = (
    "CDS-SIGN|interop-fixture|CDS-INTEROP|buyer|2026-10-16T00:00:00.000Z"
    "|purpose:p|purpose_scope:read"
)
INTEROP_SIG = (
    "bd372f7db138b0a5a45448c12388d717e507225bb71d500827634b7f869dfa12"
    "761af956d35735f394f0382daef8bff197f0c4f294c7245dc4878410bfe00058"
)


def _reference_digest(public_key: str, message: bytes) -> str:
    """按 GM/T 0003.2 5.2 原文独立实现的参考摘要（不共享被测代码的常量表）。"""
    from gmssl.sm3 import sm3_hash

    entl = (len(UID) * 8).to_bytes(2, "big")
    z_input = entl + UID.encode() + bytes.fromhex(
        CURVE_A + CURVE_B + CURVE_GX + CURVE_GY + public_key[:64] + public_key[64:]
    )
    z = sm3_hash(list(z_input))
    return sm3_hash(list(bytes.fromhex(z) + message))


def test_digest_follows_gmt_0003_2_z_formula():
    for message in (b"", b"CDS-SIGN|x|y|buyer|T", INTEROP_MSG.encode()):
        assert crypto_service._sm3_hash_with_z(message, INTEROP_PUB) == _reference_digest(
            INTEROP_PUB, message
        )


def test_standard_third_party_signature_verifies():
    assert crypto_service.verify(INTEROP_MSG.encode(), INTEROP_SIG, INTEROP_PUB) is True


def test_signature_does_not_verify_after_payload_change():
    tampered = INTEROP_MSG.replace("purpose_scope:read", "purpose_scope:read,aggregate")
    assert crypto_service.verify(tampered.encode(), INTEROP_SIG, INTEROP_PUB) is False


def test_service_signed_message_is_standard_verifiable():
    """服务端自己签的东西必须能被标准摘要验过 —— 否则外部审计方无法验真。"""
    keypair = crypto_service.generate_keypair()
    message = INTEROP_MSG.encode()
    signed = crypto_service.sign(message, keypair.private_key, keypair.public_key)
    assert crypto_service.verify(message, signed.signature, keypair.public_key) is True
    assert (
        crypto_service._sm3_hash_with_z(message, keypair.public_key)
        == _reference_digest(keypair.public_key, message)
    )
