"""D-e2e-14：信封解密不可用必须失败，不能把密文当明文返回。

旧 `storage_service.download()` 把自建的 `ValueError("Key ... not found in KMS")`
又被同一个 `except (ValueError, struct.error): pass` 吞掉，于是信封对象在密钥丢失
（KMS 内存态重启）后原样返回密文。下游 `jupyter_gateway.inject_contract_data`
因此报「已注入 1 个文件」，而 kernel 里 `pd.read_csv` 直接 UnicodeDecodeError。
"""

from __future__ import annotations

import pytest

from app.services.storage_service import StorageService
from app.utils.crypto import SM4Cipher

PLAIN = "order_id,region,amount\nO1,大连区域,622.09\n".encode("utf-8")


def _svc(raw: bytes, kms) -> StorageService:
    svc = StorageService()
    svc._kms = kms
    svc._download_raw = lambda object_name: raw
    return svc


def _envelope(svc: StorageService, key_id: str, dek: bytes) -> bytes:
    ct, nonce, tag = SM4Cipher(dek).encrypt_gcm(PLAIN)
    return svc._pack_envelope(key_id, nonce, tag, ct)


class _KMS:
    def __init__(self, keys: dict[bytes | None]):
        self._keys = keys

    def get_key(self, key_id: str):
        return self._keys.get(key_id)


def test_missing_key_raises_instead_of_returning_ciphertext():
    svc = StorageService()
    dek = bytes(range(32))
    raw = _envelope(svc, "dek-gone", dek)
    svc2 = _svc(raw, _KMS({}))
    with pytest.raises(ValueError) as exc:
        svc2.download("data-resources/orders.csv")
    assert "dek-gone" in str(exc.value)


def test_tampered_tag_raises_instead_of_returning_ciphertext():
    svc = StorageService()
    dek = bytes(range(32))
    raw = bytearray(_envelope(svc, "dek-bad", dek))
    raw[-1] ^= 0xFF
    svc2 = _svc(bytes(raw), _KMS({"dek-bad": dek}))
    with pytest.raises(ValueError):
        svc2.download("data-resources/orders.csv")


def test_valid_envelope_still_round_trips():
    svc = StorageService()
    dek = bytes(range(32))
    raw = _envelope(svc, "dek-ok", dek)
    assert _svc(raw, _KMS({"dek-ok": dek})).download("o.csv") == PLAIN


def test_plaintext_object_returns_bytes_unchanged():
    assert _svc(b"plain,csv,header\n1,2,3\n", _KMS({})).download("o.csv") == b"plain,csv,header\n1,2,3\n"
