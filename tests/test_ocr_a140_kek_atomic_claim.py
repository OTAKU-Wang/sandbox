"""A140 (ocr CR 2026-10-18): `_ensure_kek` 无锁 check-then-act ⇒ 同一个 id 覆写主密钥。

修复前 `KMSService._ensure_kek` 只看进程内 bool `self._kek_ready`，事件循环线程
与 `asyncio.to_thread` worker 线程都能同时读到 False 并同时调
`hsm.generate_kek("cds-master-kek")`。覆写语义住在
`hsm_adapter.SoftwareHSMAdapter.generate_kek`（`self._keys[key_id] = kek`，
无 id 存在性检查），本轨无权修改该文件 —— A140 要求的是**调用方**必须在认领
主密钥时做到原子：已存在即复用，绝不以同一个 id 触发第二次 mint。

后果不是"多调一次随机数"：用第一把 KEK 封存的 DEK/session key wrapped blob
在覆写后永久解不开（`unwrap_wrapped` 只会记一条 error 然后返回 None），
上层据此判定数据不可恢复。
"""
import os
import threading

import pytest

from app.core.errors import CDSError
from app.services.kms_service import KMSService, _KEK_ID
from app.utils.crypto import SM4Cipher


def _seal(kek: bytes, dek: bytes) -> bytes:
    ct, nonce, tag = SM4Cipher(kek).encrypt_gcm(dek)
    return nonce + tag + ct


def _open(kek: bytes, blob: bytes) -> bytes:
    return SM4Cipher(kek).decrypt_gcm(blob[28:], blob[:12], blob[12:28])


class _OverwritingHSM:
    """软件 HSM 的替身：与真实现一样「同一个 id 无条件覆写」。

    替身复刻的是 `hsm_adapter.py` 的既有语义（`generate_kek` 直接
    `self._keys[key_id] = os.urandom(32)`），不是本次要修的行为；本文件钉的是
    `kms_service._ensure_kek` 会不会在它上面并发触发第二次 mint。
    """

    def __init__(self, *, entered=None, mint_gate=None, corrupt_mint=False):
        self.keys: dict[str, bytes] = {}
        self.generate_calls = 0
        self._entered = entered
        self._mint_gate = mint_gate
        self._corrupt_mint = corrupt_mint
        self._lock = threading.Lock()

    def generate_kek(self, key_id: str) -> bytes:
        with self._lock:
            self.generate_calls += 1
            if self._corrupt_mint:
                # 声称 mint 成功，但 HSM 里实际没有可用的这把 KEK。
                return os.urandom(32)
            self.keys[key_id] = os.urandom(32)
            minted = self.keys[key_id]
        if self._entered is not None:
            self._entered.release()
        if self._mint_gate is not None:
            self._mint_gate.wait(5.0)
        return minted

    def wrap_dek(self, kek_id: str, dek_bytes: bytes) -> bytes:
        kek = self.keys.get(kek_id)
        if not kek:
            raise ValueError(f"KEK {kek_id} not found")
        return _seal(kek, dek_bytes)

    def unwrap_dek(self, kek_id: str, wrapped_dek: bytes) -> bytes:
        kek = self.keys.get(kek_id)
        if not kek:
            raise ValueError(f"KEK {kek_id} not found")
        return _open(kek, wrapped_dek)


def test_concurrent_ensure_kek_mints_the_master_key_once():
    """并发认领主密钥只能 mint 一次，且先封存的 DEK 必须仍然解得开。"""
    entered = threading.Semaphore(0)
    gate = threading.Event()
    hsm = _OverwritingHSM(entered=entered, mint_gate=gate)
    svc = KMSService()
    svc._hsm = hsm
    svc._kek_ready = False

    start = threading.Barrier(4)
    errors: list[BaseException] = []

    def _worker():
        try:
            start.wait(5.0)
            svc._ensure_kek()
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(4)]
    for t in threads:
        t.start()

    # 第一把 KEK 落地后先封一个 DEK，再放行其余线程：修复前它们会带着同一个
    # id 走进 generate_kek 并覆写掉它。
    assert entered.acquire(timeout=5.0), "no thread claimed the KEK"
    first_kek = hsm.keys[_KEK_ID]
    dek = os.urandom(32)
    sealed = _seal(first_kek, dek)
    threading.Event().wait(0.2)  # 开窗口：无锁实现此刻必然重复 mint
    gate.set()
    for t in threads:
        t.join(5.0)
        assert not t.is_alive()

    assert errors == [], f"_ensure_kek raised in a worker: {errors!r}"
    assert hsm.generate_calls == 1, (
        f"master KEK minted {hsm.generate_calls} times under id {_KEK_ID!r}"
    )
    assert svc.unwrap_wrapped(sealed) == dek, (
        "DEK sealed under the first KEK became undecryptable — the master key was "
        "overwritten under the same id"
    )


def test_existing_kek_is_reused_never_overwritten():
    """HSM 里已有可用 KEK ⇒ 复用，不得再 mint（重启/重建服务实例后的认领）。"""
    hsm = _OverwritingHSM()
    existing = os.urandom(32)
    hsm.keys[_KEK_ID] = existing
    dek = os.urandom(32)
    sealed = _seal(existing, dek)

    svc = KMSService()
    svc._hsm = hsm
    svc._kek_ready = False
    svc._ensure_kek()

    assert hsm.keys[_KEK_ID] == existing, "an existing master KEK was replaced"
    assert hsm.generate_calls == 0
    assert svc.unwrap_wrapped(sealed) == dek
    assert svc._kek_ready is True


def test_mint_is_claimed_only_after_it_round_trips():
    """mint 声称成功但 KEK 不可用 ⇒ 不认领、报 KMS_UNAVAILABLE，别把 DEK 封进死密钥。"""
    hsm = _OverwritingHSM(corrupt_mint=True)
    svc = KMSService()
    svc._hsm = hsm
    svc._kek_ready = False

    with pytest.raises(CDSError) as exc:
        svc._ensure_kek()
    assert exc.value.code == "KMS_UNAVAILABLE"
    assert exc.value.http_status == 503
    assert svc._kek_ready is False, "a KEK that cannot wrap/unwrap was marked ready"


def test_sequential_caller_does_not_rehash_the_ceiling():
    """认领一次之后重复调用只读标志位，不再触碰 HSM（防止「每次调用都重建」回归）。"""
    hsm = _OverwritingHSM()
    svc = KMSService()
    svc._hsm = hsm
    svc._kek_ready = False
    svc._ensure_kek()
    calls_after_first = hsm.generate_calls
    for _ in range(5):
        svc._ensure_kek()
    assert hsm.generate_calls == calls_after_first == 1
