"""OCR A255 — 合约签名原文的 `|` / `,` 拼接歧义（先红后绿）。

台账（docs/ocr-review-2026-10-18/ocr-verified-findings.md ### A255）：
`contract_sign_data` 用 `|` 直接拼接四个字段、用 `,` 拼接 purpose_scope 元素，
既不转义也不限长。`contract_no="X|purpose:Y"` 与 `purpose="Y"` 拼出的原文**逐字节
相同**，而这份原文的 SM2 签名就是跨方的不可否认证据 —— 一条签名同时为两种互斥
用途限定背书。修法是拼接前**拒绝**（后端已实现），本文件锁住这条性质。
"""
import pytest

from app.core.errors import CDSError
from app.services.crypto_service import CryptoService

TS = "2026-10-20T00:00:00.000Z"


def _sign(**kw) -> bytes:
    kw.setdefault("contract_id", "c-1")
    kw.setdefault("party_role", "buyer")
    kw.setdefault("timestamp", TS)
    return CryptoService.contract_sign_data(**kw)


def test_a255_embedded_pipe_in_contract_no_is_refused():
    """字段里塞 `|` 必须拒签，而不是拼出可被对方重新解释的原文。"""
    with pytest.raises(CDSError) as exc:
        _sign(contract_no="X|purpose:Y")
    assert exc.value.code == "VALIDATION_ERROR"
    assert exc.value.http_status == 422
    fields = {e["field"] for e in exc.value.detail["errors"]}
    assert "contract_no" in fields


def test_a255_ambiguous_pair_cannot_both_be_signed():
    """真碰撞对（已在 5d5417e 基线上实测同串）：`contract_id="c-1|A", contract_no="B"`
    与 `contract_id="c-1", contract_no="A|B"` 都拼出 `CDS-SIGN|c-1|A|B|buyer|T`。"""
    with pytest.raises(CDSError):
        _sign(contract_id="c-1|A", contract_no="B")
    with pytest.raises(CDSError):
        _sign(contract_id="c-1", contract_no="A|B")


def test_a255_scope_encoding_is_injective():
    """`['read,aggregate']` 与 `['read','aggregate']` 在旧实现里同串（实测），
    现在前者拒签、后者合法 —— 一条签名只能对应一种 scope 集合。"""
    legit = _sign(contract_no="N-1", purpose="p", purpose_scope=["read", "aggregate"])
    assert legit.endswith(b"purpose_scope:read,aggregate")
    with pytest.raises(CDSError):
        _sign(contract_no="N-1", purpose="p", purpose_scope=["read,aggregate"])


def test_a255_pipe_in_any_signed_field_is_refused():
    cases = {
        "contract_id": lambda: _sign(contract_id="c|1", contract_no="N-1"),
        "contract_no": lambda: _sign(contract_no="N|1"),
        "party_role": lambda: _sign(contract_no="N-1", party_role="buyer|admin"),
        "timestamp": lambda: _sign(contract_no="N-1", timestamp=f"{TS}|x"),
        "purpose": lambda: _sign(contract_no="N-1", purpose="风控|建模"),
    }
    for field, build in cases.items():
        with pytest.raises(CDSError) as exc:
            build()
        assert field in {e["field"] for e in exc.value.detail["errors"]}, field


def test_a255_comma_in_scope_element_is_refused():
    """," 分隔 scope 元素：元素里带 `,` 就能伪造出多一个 scope。"""
    with pytest.raises(CDSError) as exc:
        _sign(contract_no="N-1", purpose="风控", purpose_scope=["read,aggregate"])
    detail = exc.value.detail["errors"]
    assert any(e["field"] == "purpose_scope" and e.get("index") == 0 for e in detail)


def test_a255_overlong_field_is_refused_but_column_width_passes():
    """长度上限取 DB 列宽（contract_no / purpose 都是 String(64)）：64 合法、65 拒签。"""
    assert _sign(contract_no="N" * 64)
    with pytest.raises(CDSError) as exc:
        _sign(contract_no="N" * 65)
    assert any("characters" in e["reason"] for e in exc.value.detail["errors"])


def test_a255_scope_item_count_is_capped():
    with pytest.raises(CDSError) as exc:
        _sign(contract_no="N-1", purpose="p", purpose_scope=[f"s{i}" for i in range(65)])
    assert any(e["field"] == "purpose_scope" for e in exc.value.detail["errors"])


def test_a255_canonical_bytes_unchanged_for_legal_fields():
    """合法字段的原文字节形状不变（否则跨方验签全红 / 前端镜像失配）。"""
    assert _sign(contract_no="CDS-1") == f"CDS-SIGN|c-1|CDS-1|buyer|{TS}".encode()
    assert (_sign(contract_no="CDS-1", purpose="风控建模")
            == f"CDS-SIGN|c-1|CDS-1|buyer|{TS}|purpose:风控建模".encode())
    assert (_sign(contract_no="CDS-1", purpose="风控建模", purpose_scope=["read", "aggregate"])
            == f"CDS-SIGN|c-1|CDS-1|buyer|{TS}|purpose:风控建模|purpose_scope:read,aggregate".encode())
    # 空 scope 与 None 同串（与前端镜像的 length>0 判断一致）
    assert _sign(contract_no="CDS-1", purpose="p", purpose_scope=[]) == _sign(contract_no="CDS-1", purpose="p")
