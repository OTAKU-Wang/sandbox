"""OCR A184（续片）—— mTLS 证书载荷单一构造点 + verify_certificate 的入口校验与异常方向。

签发/校验两侧口径不一致的半边已由前一片在 5aa84a8 改掉（`issue_certificate` 也改成
`if space_id:`），`tests/test_ocr_a184_a027.py` 钉住。台账 A184 剩余两点：
1. 两侧仍是**两份手写代码**——任何一侧加/删字段就会再次漂移（这正是原缺陷的形状），
   故改为共用同一个 `build_cert_data(cert)`；本文件用「签名侧与验签侧收到的载荷字节必须
   完全相同」把它钉住。
2. `verify_certificate` 入口没有 tz/格式校验：对端反序列化出的 naive `datetime` 会让
   `now < cert.not_before` 抛 `TypeError`；`return self._crypto.verify(...)` 外面没有
   `try`，畸形签名（`bytes.fromhex` 的 `ValueError`、gmssl 缺失的 `RuntimeError`）会直接
   冒泡成 500，而不是「验不过 = False」。验真函数的异常方向必须是拒绝，不是崩溃。
"""
import hashlib
import hmac
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

from app.services.mutual_tls import CertificateInfo, SM2CertificateAuthority


class _RecordingCrypto:
    """确定性 SM2 替身：记录两侧实际送去签名/验签的载荷字节。"""

    secret = b"cds-test-ca"

    def __init__(self):
        self.signed: list[bytes] = []
        self.verified: list[bytes] = []

    def _mac(self, payload: bytes) -> str:
        return hmac.new(self.secret, payload, hashlib.sha256).hexdigest()

    def sign(self, payload, priv, pub):
        self.signed.append(bytes(payload))
        return types.SimpleNamespace(signature=self._mac(payload))

    def verify(self, payload, signature, pub):
        self.verified.append(bytes(payload))
        if signature is None or not isinstance(signature, str):
            raise ValueError(f"malformed signature: {signature!r}")
        try:
            bytes.fromhex(signature)
        except ValueError as exc:  # 真 gmssl 路径同样是 fromhex 抛错
            raise ValueError(str(exc)) from exc
        return hmac.compare_digest(self._mac(payload), signature)


def _ca() -> tuple[SM2CertificateAuthority, _RecordingCrypto]:
    crypto = _RecordingCrypto()
    ca = SM2CertificateAuthority.__new__(SM2CertificateAuthority)
    ca._crypto = crypto
    ca._ca_keypair = types.SimpleNamespace(private_key="priv", public_key="pub")
    return ca, crypto


@pytest.mark.parametrize("space_id", ["", "space-42"])
def test_a184_sign_and_verify_payloads_are_identical(space_id):
    """签发侧与校验侧的载荷必须由同一函数产出——字节级相同。"""
    ca, crypto = _ca()
    root = ca.generate_ca_certificate()
    cert = ca.issue_certificate(space_id=space_id, public_key="peer-pub", subject="node-a")

    assert ca.verify_certificate(cert, root) is True
    assert crypto.verified[-1] == crypto.signed[-1]


def test_a184_ca_self_signed_payload_round_trips():
    ca, crypto = _ca()
    root = ca.generate_ca_certificate()
    assert ca.verify_certificate(root, root) is True
    assert crypto.verified[-1] == crypto.signed[0]


def test_a184_naive_datetimes_are_rejected_not_crashing():
    """对端给出无 tz 的 datetime ⇒ 验不过（False），而不是 TypeError。"""
    ca, _ = _ca()
    root = ca.generate_ca_certificate()
    cert = ca.issue_certificate(space_id="s", public_key="peer-pub", subject="n")

    cert.not_before = datetime.now(timezone.utc).replace(tzinfo=None)
    assert ca.verify_certificate(cert, root) is False

    cert2 = ca.issue_certificate(space_id="s", public_key="peer-pub", subject="n")
    cert2.not_after = "2030-01-01T00:00:00"  # 反序列化成了字符串
    assert ca.verify_certificate(cert2, root) is False


def test_a184_malformed_signature_returns_false():
    """畸形签名必须是 False，不能把 crypto 的异常冒泡给调用方。"""
    ca, _ = _ca()
    root = ca.generate_ca_certificate()
    cert = ca.issue_certificate(space_id="s", public_key="peer-pub", subject="n")

    cert.signature = "zorro-not-hex"
    assert ca.verify_certificate(cert, root) is False

    cert.signature = None
    assert ca.verify_certificate(cert, root) is False


def test_a184_cert_data_builder_is_defined_after_the_dataclass():
    """py<=3.13 在 def 期求值注解：前向引用 = 导入期 NameError（生产镜像 py3.12）。"""
    import inspect

    src = inspect.getsource(sys.modules[CertificateInfo.__module__])
    assert src.index("class CertificateInfo") < src.index("def build_cert_data")
    assert "from __future__ import annotations" not in src


def test_a184_expired_cert_still_rejected():
    ca, _ = _ca()
    root = ca.generate_ca_certificate()
    cert = ca.issue_certificate(space_id="s", public_key="peer-pub", subject="n")
    now = datetime.now(timezone.utc)
    cert.not_before = now - timedelta(days=400)
    cert.not_after = now - timedelta(days=1)
    assert ca.verify_certificate(cert, root) is False
