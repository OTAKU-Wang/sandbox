"""OCR 复核轮 A184/A27 —— mTLS 载荷对称性与缓存失败口径的回归测试。

- A184 `mutual_tls.py`：签发侧（`:136`）无条件把 `space_id` 写进签名载荷，校验侧
  （`:186-187`）只在真值时才写 ⇒ `space_id=""` 的证书**签名永远验不过**（载荷两边
  不一致），而这条路径不需要任何攻击者配合。
- A27 `app/core/cache.py`：`cache_set` 把 `json.dumps` 的 `TypeError`（调用方传了
  不可序列化对象，是程序错误）和 Redis 连接错误一起 `except Exception` 吞掉，
  调用方以为写成功，表现为「缓存永不命中」型隐故障；`cache_clear(pattern)` 把
  `pattern` 直拼进 `MATCH cache:{pattern}*`，glob 元字符未转义。
"""
import hashlib
import hmac
import types

import pytest

from app.core import cache
from app.services.mutual_tls import SM2CertificateAuthority


class _FakeCrypto:
    """确定性的 SM2 替身：只复刻 sign/verify 的载荷-签名字节语义。"""

    secret = b"cds-test-ca"

    def _mac(self, payload: bytes) -> str:
        return hmac.new(self.secret, payload, hashlib.sha256).hexdigest()

    def sign(self, payload, priv, pub):
        return types.SimpleNamespace(signature=self._mac(payload))

    def verify(self, payload, signature, pub):
        return hmac.compare_digest(self._mac(payload), signature)


def _ca() -> SM2CertificateAuthority:
    ca = SM2CertificateAuthority.__new__(SM2CertificateAuthority)
    ca._crypto = _FakeCrypto()
    ca._ca_keypair = types.SimpleNamespace(private_key="priv", public_key="pub")
    return ca


def test_a184_cert_with_empty_space_id_verifies():
    ca = _ca()
    root = ca.generate_ca_certificate()
    cert = ca.issue_certificate(space_id="", public_key="peer-pub", subject="node-a")
    assert ca.verify_certificate(cert, root) is True


def test_a184_cert_with_space_id_still_verifies():
    ca = _ca()
    root = ca.generate_ca_certificate()
    cert = ca.issue_certificate(space_id="space-42", public_key="peer-pub", subject="node-b")
    assert ca.verify_certificate(cert, root) is True


def test_a184_tampered_payload_is_rejected():
    ca = _ca()
    root = ca.generate_ca_certificate()
    cert = ca.issue_certificate(space_id="space-42", public_key="peer-pub", subject="node-c")
    cert.public_key = "attacker-pub"
    assert ca.verify_certificate(cert, root) is False


async def _fake_redis(monkeypatch, recorder):
    class _Fake:
        async def setex(self, key, ttl, value):
            recorder.setdefault("setex", []).append((key, ttl, value))

        async def scan(self, cursor, match=None, count=None):
            recorder.setdefault("match", []).append(match)
            return 0, []

        async def delete(self, *keys):
            return 0

    async def _get():
        return _Fake()

    monkeypatch.setattr(cache, "_get_redis", _get)


async def test_a027_cache_set_raises_on_unserializable_value(monkeypatch):
    await _fake_redis(monkeypatch, {})
    with pytest.raises(TypeError):
        await cache.cache_set("k", object(), ttl_seconds=60)


async def test_a027_cache_set_still_tolerates_redis_outage(monkeypatch):
    class _Down:
        async def setex(self, key, ttl, value):
            raise ConnectionError("redis down")

    async def _get():
        return _Down()

    monkeypatch.setattr(cache, "_get_redis", _get)
    await cache.cache_set("k", {"ok": 1}, ttl_seconds=60)


async def test_a027_cache_clear_escapes_glob_metachars(monkeypatch):
    recorder = {}
    await _fake_redis(monkeypatch, recorder)
    await cache.cache_clear("orders:v1*?")
    pattern = recorder["match"][0]
    assert pattern == "cache:orders:v1\\*\\?*"
