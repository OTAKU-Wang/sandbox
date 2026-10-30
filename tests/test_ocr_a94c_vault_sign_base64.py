"""OCR A94c — 真 Vault 签名路径 100% 抛 UnboundLocalError（既存真缺陷，本地绿灯对它结构性失明）。

`VaultTransitAdapter.sign()` 在 `input=base64.b64encode(data)` 里用 `base64`，而同一函数
后面才有 `import base64`。Python 在编译期把 `base64` 判成该函数的**局部名**，于是第一处
使用必然 `UnboundLocalError` —— 它被同函数的 `except Exception` 吞成一行 warning，对外只剩
`RuntimeError("HSM unavailable for signing")`，看起来像「Vault 不能签」而不是「我们没导入」。
本机的软件 HSM 走 `SoftwareHSMAdapter.sign`，Vault 分支此前没有任何本地测试覆盖。

本文件不 mock 被测语句：只注入一个假的 hvac 客户端对象（提供 transit.sign_data 的 REST
形状返回值），让**真实的签名分支**跑完，断言拿到的是 `vault:v1:<base64>` 解出来的签名字节、
且送进 REST 的 input 是数据的合法 base64。真 Vault/hvac 不在本机 ⇒ 这只证明分支可达且口径
正确，不证明真后端。
"""
import ast
import base64
import inspect
import logging
import textwrap
import types

from app.services.hsm_adapter import VaultTransitAdapter

_RAW_SIG = bytes(range(1, 65))


class _FakeTransit:
    def __init__(self):
        self.calls: list[dict[str, object]] = []

    def sign_data(self, name: str, input: str, hash_algorithm: str = "sha2-256"):
        self.calls.append({"name": name, "input": input, "hash_algorithm": hash_algorithm})
        return {"data": {"signature": "vault:v1:" + base64.b64encode(_RAW_SIG).decode()}}


class _FakeClient:
    def __init__(self):
        self.secrets = types.SimpleNamespace(transit=_FakeTransit())


def _wired() -> tuple[VaultTransitAdapter, _FakeClient]:
    adapter = VaultTransitAdapter("http://vault.invalid:8200", "token-not-used")
    fake = _FakeClient()
    setattr(adapter, "_client", fake)
    return adapter, fake


def test_vault_sign_returns_the_decoded_signature():
    adapter, fake = _wired()
    sig = adapter.sign(b"payload-to-sign", "cds-signing")
    assert sig == _RAW_SIG, "签名分支没能从 vault:v1:<base64> 里解出签名字节"
    sent = fake.secrets.transit.calls[0]["input"]
    assert base64.b64decode(sent) == b"payload-to-sign", (
        "transit REST 契约是 base64 入、base64 出（D-e2e-13）；送出去的必须是数据的 base64"
    )


def test_vault_sign_swallows_nothing(caplog):
    """分支跑通时不该有任何 warning —— 旧行为是把 UnboundLocalError 吞成「Vault 不能签」。"""
    adapter, _ = _wired()
    with caplog.at_level(logging.WARNING, logger="app.services.hsm_adapter"):
        adapter.sign(b"payload-to-sign", "cds-signing")
    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert not msgs, f"签名路径上有被吞掉的异常: {msgs}"


def test_sign_has_no_function_local_base64_import():
    """结构性护栏：函数内再 import 一次 base64 会把模块名遮蔽成局部名（A94c 根因）。"""
    fn = ast.parse(textwrap.dedent(inspect.getsource(VaultTransitAdapter.sign))).body[0]
    shadowing = [
        node for node in ast.walk(fn)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and any(a.name == "base64" for a in node.names)
    ]
    assert not shadowing, (
        "函数体内的 `import base64` 让 base64 成为局部名，"
        "导致同一函数更早的 b64encode 必然 UnboundLocalError"
    )
