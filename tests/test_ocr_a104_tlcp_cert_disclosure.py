"""A104: TLCP 活路径上的证书 DER 族——产出的 PEM 严格解析器读不了，但服务照发不误（high）。

`app/services/tlcp_service.py` 的 `_build_cert_pem()` 把 DER 构造委托给
`certificate_service._build_cert_pem()`（手搓 X.509，非本文件）。本机实测：cryptography
能载入顶层，但读任何扩展都会抛 `ParseError { kind: InvalidValue }`，签名是 64 字节裸
r||s 而不是 DER `ECDSA-Sig-Value` ⇒ 真 TLCP 对端（铜锁/OpenSSL）必然拒。改前本服务对此
一无所知：`generate_certificate()` 只回「有 PEM、有指纹」，`verify_certificate()` 用手搓
解析器把自己产的证书判成 valid，而 `app/api/auth.py:554`、`app/api/certificates.py` 就
在把这些结果给上层。

本条把「披露 + 不装」修在本文件里：证书对象带 `interop_ok/interop_error/
fingerprint_alg/crypto_backend`，`verify_certificate()` 的每个返回分支带 `interop`，指纹
改走仓内受 `CDS_SM3_FALLBACK_ALLOWED` 门控的 `sm3_hash`（原来是未披露的 sha256 兜底）。
DER 构造本身（KeyUsage 位序/critical、签名要 DER 编码）属 `certificate_service.py`
⇒ NEEDS-OWNER，本文件不越界改。
"""
import logging
import sys

import pytest

from app.core.config import get_settings
from app.services import tlcp_service as ts


@pytest.fixture()
def svc(tmp_path):
    return ts.TLCPService(cert_store_path=str(tmp_path / "certs"))


def test_generated_cert_discloses_strict_parser_rejection(svc, caplog):
    from cryptography import x509

    with caplog.at_level(logging.WARNING):
        cert = svc.generate_certificate("api.cds.local")

    # A104 DER 已修：严格解析器必须把这张证书读干净
    assert cert.interop_ok is True, f"DER 修复后仍读不干净: {cert.interop_error}"
    assert cert.interop_error == ""

    parsed = x509.load_pem_x509_certificate(cert.cert_pem.encode())
    assert [ext.value for ext in parsed.extensions], "扩展必须能读回"
    assert parsed.signature[:1] == b"\x30", "signatureValue 必须是 DER ECDSA-Sig-Value"
    assert not any(
        "NOT verifiable by a strict X.509" in r.getMessage() for r in caplog.records
    ), "严格解析器读不了却不吭声，就是拿国密证书的名义发出去"


def test_verify_certificate_carries_interop_without_changing_valid(svc):
    cert = svc.generate_certificate("api.cds.local")

    result = svc.verify_certificate(cert.cert_pem)

    assert result["valid"] is True
    assert result["interop"]["parseable"] is True
    assert result["interop"]["error"] == ""


def test_fingerprint_and_backend_are_disclosed(svc):
    from app.utils.crypto import sm3_hash

    cert = svc.generate_certificate("api.cds.local")

    assert cert.fingerprint_alg == "sm3"
    assert cert.fingerprint == sm3_hash(svc._pem_to_der(cert.cert_pem, "CERTIFICATE"))
    assert "Tongsuo" not in cert.crypto_backend
    assert "no TLCP-capable OpenSSL" in cert.crypto_backend


def test_sha256_fingerprint_fallback_is_gated_and_labelled(svc, monkeypatch):
    monkeypatch.setitem(sys.modules, "gmssl", None)
    monkeypatch.setitem(sys.modules, "gmssl.sm3", None)
    # 进程级一次性告警位：不走下面这条 setattr 就会把 True 溹给同批其它测试
    # （`test_pz04_honesty_gates::test_sm3_hash_uses_real_sm3_when_gmssl_present` 直接断言它为 False），
    # teardown 会原值归还 —— 与 pz04 自己的兜底用例同一写法。
    import app.utils.crypto as crypto

    monkeypatch.setattr(crypto, "_sm3_fallback_warned", False)
    settings = get_settings()

    monkeypatch.setattr(settings, "SM3_FALLBACK_ALLOWED", True, raising=False)
    cert = svc.generate_certificate("fallback.cds.local")
    assert cert.fingerprint_alg == "sha256-fallback"

    monkeypatch.setattr(settings, "SM3_FALLBACK_ALLOWED", False, raising=False)
    with pytest.raises(RuntimeError):
        svc.generate_certificate("blocked.cds.local")


def test_strict_parse_report_is_honest_about_a_good_pem(svc):
    from datetime import datetime, timedelta, timezone
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import hashes, serialization

    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(timezone.utc)
    der = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(x509.OID_COMMON_NAME, "good")]))
        .issuer_name(x509.Name([x509.NameAttribute(x509.OID_COMMON_NAME, "good")]))
        .public_key(key.public_key())
        .serial_number(7)
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                     key_encipherment=False, data_encipherment=False,
                                     key_agreement=False, key_cert_sign=False,
                                     crl_sign=False, encipher_only=False, decipher_only=False),
                       critical=True)
        .sign(private_key=key, algorithm=hashes.SHA256())
    )
    pem = der.public_bytes(serialization.Encoding.PEM).decode()

    ok, error = ts._strict_parse_report(pem)

    assert ok is True, error
    assert error == ""


def test_strict_parse_report_does_not_pass_a_broken_pem():
    ok, error = ts._strict_parse_report("-----BEGIN CERTIFICATE-----\nzzz\n-----END CERTIFICATE-----\n")

    assert ok is False
    assert error
