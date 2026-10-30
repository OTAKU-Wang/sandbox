"""A104（high，本轨只做 DER 构造半边）— 手搓 X.509 产出的 PEM 严格解析器读不了。

台账: docs/ocr-review-2026-10-18/ocr-verified-findings.md §A104

`tlcp_service._build_cert_pem()` 把 DER 构造委托给 `certificate_service._build_cert_pem()`，
而那条路是手写 TLV。改前本机实测（cryptography 46 / OpenSSL 3.6.2，两者都是严格解析器）：

* `x509.load_pem_x509_certificate()` 顶层能载入，但 `list(cert.extensions)` 抛
  `ValueError: error parsing asn1 value: ParseError { kind: InvalidValue }` —— KeyUsage 的
  BIT STRING 写成 `03 02 01 05`：`unused bits=1` 却把最低位置 1（DER 禁止），且位序按 LSB 数，
  于是"数字签名+密钥加密"的终端实体被读成 **keyCertSign + encipherOnly**（能给自己签证书）；
* CA 证书的 BasicConstraints 用 `INTEGER 1` 顶替 `BOOLEAN`，严格解析器把它读成 `ca=FALSE`
  并抱怨 `path_length must be None when ca is False`；
* `signatureValue` 直接写裸 `r||s` 64 字节，不是 DER `ECDSA-Sig-Value`；
* `_parse_x509_der()` 里 `tbs_buf[0] & 0xa0 == 0xa0` / `tbs_buf[idx] & 0xa0 == 0xa3` 因运算符
  优先级（`&` 低于 `==`）整段死代码：version 恒为 1、serial 读错位置、扩展块从不执行、
  `is_ca` 退化成"扩展存在即 CA"。

⇒ 活路径 `app/api/auth.py` / `app/api/certificates.py` 把这种 PEM 当"国密证书"发出去，
对端（OpenSSL/BabaSSL 标准链）拒收，而自研解析器始终报"有效"。

本文件钉构造面（`app/services/certificate_service.py`）：扩展必须**被严格解析器读回且值正确**，
不是"载入不抛"。披露面（`tlcp_service` 的 `interop_ok`）由 backend-21 落，其共享用例
`tests/test_ocr_a104_tlcp_cert_disclosure.py` 里钉"改前必然不兼容"的两条断言按作者留言
（"签名形状变了就说明 DER 已修，本条披露要跟着改"）需要跟着翻，本轨不越界改它。

未验：真 Tongsuo/BabaSSL 握手、真 SM2 硬件；本机只有软件 gmssl + OpenSSL 3.6.2 的解析面。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.certificate_service import (
    _encode_integer,
    certificate_service,
)
from app.services.crypto_service import crypto_service
from app.services.tlcp_service import _strict_parse_report

from cryptography import x509

import base64
import re

NOW = datetime.now(timezone.utc)


def _der(pem: str) -> bytes:
    return base64.b64decode(re.sub(r"-----.*?-----|\s", "", pem))


def _cert(is_ca: bool) -> tuple[str, dict[str, str]]:
    keypair = crypto_service.generate_keypair()
    pem = certificate_service._build_cert_pem(
        serial=f"A104-{'CA' if is_ca else 'EE'}-{keypair.public_key[:8]}",
        subject="CN=a104.example,O=CDS,C=CN",
        issuer="CN=a104 ca,O=CDS,C=CN",
        public_key=keypair.public_key,
        not_before=NOW,
        not_after=NOW + timedelta(days=365),
        is_ca=is_ca,
        private_key=keypair.private_key,
    )
    return pem, {"private_key": keypair.private_key, "public_key": keypair.public_key}


def _serial_int(serial_label: str) -> int:
    return int.from_bytes(serial_label.encode("utf-8")[:20].rjust(20, b"\x00"), "big")


def _read_tlv(buf: bytes, at: int) -> tuple[int, bytes, int]:
    tag = buf[at]
    length = buf[at + 1]
    if length & 0x80:
        extra = length & 0x7F
        length = int.from_bytes(buf[at + 2:at + 2 + extra], "big")
        start = at + 2 + extra
    else:
        start = at + 2
    return tag, buf[start:start + length], start + length


def _bare_signature(signature: bytes) -> bytes:
    """Decode DER ECDSA-Sig-Value → raw r||s (asserts the shape on the way)."""
    assert signature[:1] == b"\x30", (
        f"signatureValue 不是 DER SEQUENCE，而是 {len(signature)} 字节裸 r||s"
    )
    _, body, _ = _read_tlv(signature, 0)
    parts: list[tuple[int, bytes]] = []
    at = 0
    while at < len(body):
        tag, value, at = _read_tlv(body, at)
        parts.append((tag, value))
    assert len(parts) == 2 and all(tag == 0x02 for tag, _ in parts), (
        f"signatureValue 不是 SEQUENCE{{r INTEGER, s INTEGER}}：{[t for t, _ in parts]}"
    )
    r, s = (int.from_bytes(value, "big") for _, value in parts)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def test_end_entity_extensions_read_back_and_cannot_sign_certificates():
    pem, _ = _cert(is_ca=False)
    parsed = x509.load_pem_x509_certificate(pem.encode())

    ku = parsed.extensions.get_extension_for_class(x509.KeyUsage)
    assert ku.critical is True, "KeyUsage 未标 critical：忽略扩展的对端能白拿签证书权"
    usage = ku.value
    assert usage.key_cert_sign is False, (
        "终端实体拿到了 keyCertSign（位序写反的产物），它能自签下级证书"
    )
    assert usage.crl_sign is False
    assert usage.digital_signature is True, f"digitalSignature 丢了：{usage}"
    assert usage.key_encipherment is True, f"keyEncipherment 丢了：{usage}"

    bc = parsed.extensions.get_extension_for_class(x509.BasicConstraints)
    assert bc.critical is True
    assert bc.value.ca is False


def test_ca_extensions_read_back_as_a_real_ca():
    pem, _ = _cert(is_ca=True)
    parsed = x509.load_pem_x509_certificate(pem.encode())

    bc = parsed.extensions.get_extension_for_class(x509.BasicConstraints)
    assert bc.critical is True
    assert bc.value.ca is True, "CA 证书的 BasicConstraints 被严格解析器读成 ca=FALSE"
    assert bc.value.path_length is None

    usage = parsed.extensions.get_extension_for_class(x509.KeyUsage).value
    assert usage.key_cert_sign is True
    assert usage.crl_sign is True
    assert usage.digital_signature is False


def test_extensions_are_marked_critical_in_the_der_itself():
    """语义读回之外，再钉一次字节：OID 之后必须紧跟 `01 01 FF`（BOOLEAN TRUE）。"""
    ee = _der(_cert(is_ca=False)[0])
    ca = _der(_cert(is_ca=True)[0])
    for der in (ee, ca):
        assert b"\x55\x1d\x0f\x01\x01\xff" in der, "keyUsage 缺少 critical 标志"
        assert b"\x55\x1d\x13\x01\x01\xff" in der, "basicConstraints 缺少 critical 标志"
    assert b"\x55\x1d\x13\x01\x01\xff\x04\x05\x30\x03\x01\x01\xff" in ca, (
        "CA 的 basicConstraints 必须是 SEQUENCE{BOOLEAN TRUE}"
    )
    assert b"\x55\x1d\x13\x01\x01\xff\x04\x02\x30\x00" in ee, (
        "终端实体的 basicConstraints 必须是空 SEQUENCE（ca DEFAULT FALSE）"
    )


def test_signature_value_is_der_ecdsa_sig_value_and_still_verifies():
    pem, _ = _cert(is_ca=False)
    parsed = x509.load_pem_x509_certificate(pem.encode())
    raw = _bare_signature(parsed.signature)
    assert len(raw) == 64, "ECDSA-Sig-Value 解不回 64 字节 r||s"
    r, s = int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
    assert 0 < r < 2**256 and 0 < s < 2**256

    result = certificate_service.verify_certificate(pem)
    assert result["signature_valid"] is True, f"验签失败：{result}"
    assert len(result["signature"]) == 128, (
        "_parse_x509_der 必须把 DER 签名还原成裸 r||s 交给 crypto_service.verify"
    )
    assert result["signature"] == raw.hex()



def test_repo_self_parser_no_longer_skips_version_and_extensions():
    """`tbs_buf[0] & 0xa0 == 0xa0` 这类优先级写法让自研解析器一直读错一位。"""
    ee_pem, _ = _cert(is_ca=False)
    ca_pem, _ = _cert(is_ca=True)

    ee = certificate_service._parse_x509_der(_der(ee_pem))
    ca = certificate_service._parse_x509_der(_der(ca_pem))

    assert ee.get("version") == 3, f"version 块被跳过（恒 1）：{ee.get('version')}"
    assert ee["subject"] == "CN=a104.example,O=CDS,C=CN"
    assert ee["issuer"] == "CN=a104 ca,O=CDS,C=CN"
    assert ee.get("is_ca") is False, f"终端实体被读成 CA：{ee.get('is_ca')}"
    assert ee.get("key_usage") == ["digitalSignature", "keyEncipherment"], ee.get(
        "key_usage"
    )
    assert ca.get("version") == 3
    assert ca.get("is_ca") is True, "真 CA 的 is_ca 读不出来（扩展块是死代码）"
    assert ca.get("key_usage") == ["keyCertSign", "cRLSign"], ca.get("key_usage")


def test_self_parser_serial_matches_the_encoded_integer():
    """护栏：serial INTEGER 必须就是那个整数的最小 DER（不多不少前导零）。"""
    label = "A104-SERIAL-CHECK"
    keypair = crypto_service.generate_keypair()
    pem = certificate_service._build_cert_pem(
        serial=label,
        subject="CN=a104,O=CDS,C=CN",
        issuer="CN=a104 ca,O=CDS,C=CN",
        public_key=keypair.public_key,
        not_before=NOW,
        not_after=NOW + timedelta(days=1),
        is_ca=False,
        private_key=keypair.private_key,
    )
    der = _der(pem)
    serial_int = _serial_int(label)
    assert _encode_integer(serial_int) in der, (
        "证书里的 serial INTEGER 不是该整数的最小 DER 编码"
    )
    assert certificate_service._parse_x509_der(der).get("serial_number") == serial_int


def test_strict_parser_used_by_tlcp_now_accepts_both_shapes():
    """与 backend-21 的披露面共用同一个判据：改后 interop 必须是干净的。"""
    for is_ca in (False, True):
        pem, _ = _cert(is_ca=is_ca)
        ok, error = _strict_parse_report(pem)
        assert ok is True, f"is_ca={is_ca} 仍被严格解析器拒绝：{error}"
