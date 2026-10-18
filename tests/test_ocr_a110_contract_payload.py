"""OCR-A110 / A255 / A113 — 合约签名原文的跨方可伪造性与 HSM 回退谎言。

A110（critical）：SM2 签名原文用 `|` 直接拼接且不转义，`purpose` 里塞
`|purpose_scope:X` 与真的给出 `purpose_scope=["X"]` 得到**同一串字节** ——
一份签名同时为两种互斥的用途限定背书；合约号/用途出自对方提交的资料，
而 SM2 签名就是双方的不可否认证据。
A255（medium-high）：四个字段既不转义也不限长。
A113（high）：`sign_with_hsm` 的"软件回退"是对同一调用的重复调用，日志预告
不存在的回退路径。

修法（与本仓跨语言锁并存）：拼接前**拒绝**含分隔符 / 超列宽的字段
（长度上限取 DB 列宽，见 app/models/contract.py），合法字段的字节输出
与 cds-frontend/src/utils/crypto.ts 的 contractSignPayload 保持逐字一致。
"""
import pytest

from app.core.errors import CDSError
from app.services.crypto_service import crypto_service

CID = "11111111-2222-3333-4444-555555555555"
TS = "2026-09-06T00:00:00+00:00"


# ─── A110: 分隔符注入 ────────────────────────────────────────────────────────

def test_purpose_cannot_smuggle_purpose_scope_segment():
    """purpose 里塞 `|purpose_scope:…` 必须被拒，而不是拼出与真 scope 同串的原文。"""
    with pytest.raises(CDSError) as exc:
        crypto_service.contract_sign_data(
            CID, "C-1", "provider", TS, purpose="研究|purpose_scope:营销",
        )
    assert exc.value.code == "VALIDATION_ERROR"
    assert exc.value.http_status == 422


def test_forged_payload_no_longer_collides_with_real_scope():
    """被拒的注入串绝不能与「真给了这个 scope」的原文相等（跨方背书歧义）。"""
    real = crypto_service.contract_sign_data(
        CID, "C-1", "provider", TS, purpose="研究", purpose_scope=["营销"],
    ).decode()
    try:
        forged = crypto_service.contract_sign_data(
            CID, "C-1", "provider", TS, purpose="研究|purpose_scope:营销",
        ).decode()
    except CDSError:
        return
    assert forged != real


def test_contract_no_separator_injection_rejected():
    with pytest.raises(CDSError) as exc:
        crypto_service.contract_sign_data(
            CID, "X|purpose:Y", "provider", TS, purpose="Y",
        )
    assert exc.value.code == "VALIDATION_ERROR"


def test_scope_element_separator_injection_rejected():
    """scope 元素含 `,` 可与相邻元素拼成同一串（列表边界同样必须无歧义）。"""
    with pytest.raises(CDSError) as exc:
        crypto_service.contract_sign_data(
            CID, "C-1", "provider", TS, purpose="研究",
            purpose_scope=["统计,建模", "统计建模"],
        )
    assert exc.value.code == "VALIDATION_ERROR"


def test_timestamp_and_role_separators_rejected():
    for kwargs in (
        {"timestamp": "2026-09-06T00:00:00+00:00|purpose:X"},
        {"party_role": "provider|purpose:X"},
        {"contract_id": CID + "|purpose:X"},
    ):
        args = {
            "contract_id": CID, "contract_no": "C-1", "party_role": "provider",
            "timestamp": TS, "purpose": None,
        }
        args.update(kwargs)
        with pytest.raises(CDSError) as exc:
            crypto_service.contract_sign_data(
                args["contract_id"], args["contract_no"], args["party_role"],
                args["timestamp"], purpose=args["purpose"],
            )
        assert exc.value.code == "VALIDATION_ERROR"


def test_validation_detail_names_the_offending_field():
    with pytest.raises(CDSError) as exc:
        crypto_service.contract_sign_data(
            CID, "C-1", "provider", TS, purpose="研究|营销",
        )
    fields = {e["field"] for e in exc.value.detail["errors"]}
    assert fields == {"purpose"}


# ─── A255: 长度上限 ──────────────────────────────────────────────────────────

def test_overlong_fields_rejected():
    """长度上限 = DB 列宽（contract_no/purpose 均 String(64)），任何合法落库值都不该被拒。"""
    with pytest.raises(CDSError) as exc:
        crypto_service.contract_sign_data(
            CID, "C-1", "provider", TS, purpose="用" * 65,
        )
    assert exc.value.code == "VALIDATION_ERROR"

    with pytest.raises(CDSError):
        crypto_service.contract_sign_data(CID, "N" * 65, "provider", TS)

    # 列宽内的合法值照常通过
    crypto_service.contract_sign_data(CID, "N" * 64, "provider", TS, purpose="用" * 64)


def test_overlong_scope_list_rejected():
    with pytest.raises(CDSError):
        crypto_service.contract_sign_data(
            CID, "C-1", "provider", TS, purpose="研究", purpose_scope=["s" * 65],
        )
    with pytest.raises(CDSError):
        crypto_service.contract_sign_data(
            CID, "C-1", "provider", TS, purpose="研究", purpose_scope=["s"] * 65,
        )


# ─── 跨语言字节锁：合法输入的输出必须逐字不变 ────────────────────────────────

def test_clean_payload_bytes_unchanged_for_frontend_mirror():
    """cds-frontend/src/utils/crypto.ts 的 contractSignPayload 逐字镜像这段。
    收紧校验不得改变合法字段的字节形状，否则对方算出的原文与服务端重算的不一致。"""
    assert crypto_service.contract_sign_data(CID, "CDS-1", "buyer", "T").decode() == (
        f"CDS-SIGN|{CID}|CDS-1|buyer|T"
    )
    assert crypto_service.contract_sign_data(
        CID, "CDS-1", "provider", "T", purpose="风控建模",
    ).decode() == f"CDS-SIGN|{CID}|CDS-1|provider|T|purpose:风控建模"
    assert crypto_service.contract_sign_data(
        CID, "CDS-1", "provider", "T", purpose="风控建模", purpose_scope=["read", "aggregate"],
    ).decode() == (
        f"CDS-SIGN|{CID}|CDS-1|provider|T|purpose:风控建模|purpose_scope:read,aggregate"
    )


def test_backward_compatible_without_purpose():
    base = crypto_service.contract_sign_data(CID, "C-1", "provider", "TS").decode()
    assert base == crypto_service.contract_sign_data(
        CID, "C-1", "provider", "TS", purpose=None, purpose_scope=None,
    ).decode()


def test_signature_still_verifies_after_validation():
    kp = crypto_service.generate_keypair()
    payload = crypto_service.contract_sign_data(CID, "C-1", "buyer", TS, purpose="统计")
    sig = crypto_service.sign(payload, kp.private_key, kp.public_key)
    assert crypto_service.verify(payload, sig.signature, kp.public_key) is True


# ─── A113: 不存在的"软件回退" ────────────────────────────────────────────────

class _FailingAdapter:
    """一个「签名必失败」的 HSM 适配器，用来数被调了几次。"""

    def __init__(self):
        self.sign_calls = 0
        self.keygen_calls = 0

    def sign(self, data, key_id):
        self.sign_calls += 1
        raise RuntimeError("HSM unavailable for signing")

    def generate_signing_keypair(self, key_id):
        self.keygen_calls += 1
        raise RuntimeError("adapter does not support SM2 signing keys")


def test_sign_with_hsm_does_not_retry_the_same_call(monkeypatch):
    """旧写法失败后把同一个调用原样再发一遍并先记一句"falling back"——
    第二次必然同样失败，日志预告的回退路径不存在。"""
    adapter = _FailingAdapter()
    monkeypatch.setattr("app.services.hsm_adapter.hsm_adapter", adapter)

    with pytest.raises(ValueError):
        crypto_service.sign_with_hsm(b"payload", "k-1")

    assert adapter.sign_calls == 1


def test_sign_with_hsm_failure_claims_no_fallback(monkeypatch, caplog):
    adapter = _FailingAdapter()
    monkeypatch.setattr("app.services.hsm_adapter.hsm_adapter", adapter)

    with caplog.at_level("DEBUG"):
        with pytest.raises(ValueError) as exc:
            crypto_service.sign_with_hsm(b"payload", "k-1")

    # 失败原因必须落在异常里，且不得留下"正在回退/已回退"的假线索
    assert "k-1" in str(exc.value)
    assert not [r for r in caplog.records if "falling back" in r.getMessage().lower()]


def test_generate_hsm_keypair_failure_claims_no_fallback(monkeypatch, caplog):
    adapter = _FailingAdapter()
    monkeypatch.setattr("app.services.hsm_adapter.hsm_adapter", adapter)

    with caplog.at_level("DEBUG"):
        with pytest.raises(RuntimeError):
            crypto_service.generate_hsm_signing_keypair("k-1")

    assert adapter.keygen_calls == 1
    assert not [r for r in caplog.records if "falling back" in r.getMessage().lower()]
