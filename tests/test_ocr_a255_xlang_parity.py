"""OCR A255 —— 合约签名原文的三语言字节平价（后端 / SDK / 前端）。

台账 `docs/ocr-review-2026-10-18/ocr-verified-findings.md` ### A255：原文用 `|`
拼接字段、用 `,` 拼接 purpose_scope 元素，不转义也不限长，跨方场景下对方可以构造
出与另一组字段**逐字节相同**的原文，而这份原文的 SM2 签名就是不可否认证据。
后端定案是拼接前**拒绝**（见 `app/services/crypto_service.py:208-216` 的注释：
原文字节被前端逐字镜像，改编码得三边同改，各自转义会验签静默失败）。

本文件不重复后端的歧义断言（那是 `tests/test_ocr_a255_payload_ambiguity.py`），
只锁这条修法真正的验收：**三边产出同一串字节，且三边对同一组越界输入一致拒签**。
唯一向量表在 `cds-frontend/src/utils/__tests__/crypto.a255-parity.test.ts`（前端
vitest 侧跑同一张表），这里按文本解析并与后端实产字节逐条复算。
"""
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

from app.core.errors import CDSError
from app.services.crypto_service import CryptoService

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "sdk"))

from cds_sdk import CDSClient  # noqa: E402
from cds_sdk import CDSError as SdkCDSError  # noqa: E402
from cds_sdk import client as sdk_client  # noqa: E402

FE_SPEC = REPO / "cds-frontend/src/utils/__tests__/crypto.a255-parity.test.ts"
FE_CRYPTO = REPO / "cds-frontend/src/utils/crypto.ts"

KEYS = ("contract_id", "contract_no", "party_role", "timestamp", "purpose", "purpose_scope")


def _table(tag: str, src: str) -> Any:
    """取前端 spec 里 `/* TAG-BEGIN */ … /* TAG-END */` 之间的 JSON 字面量。"""
    m = re.search(rf"/\* {tag}-BEGIN[^\n]*\n(.*?);\n/\* {tag}-END \*/", src, re.S)
    assert m, f"前端向量表缺少 {tag}-BEGIN/END 标记，平价无从验证：{FE_SPEC}"
    return json.loads(m.group(1).split("=", 1)[1].strip())


_src = FE_SPEC.read_text(encoding="utf-8")
LIMITS = _table("LIMITS", _src)
VECTORS = _table("VECTORS", _src)
PAYLOAD_VECTORS = [v for v in VECTORS if v["op"] == "payload"]
REJECT_VECTORS = [v for v in VECTORS if v["op"] == "reject"]

_payload_ids = pytest.mark.parametrize("v", PAYLOAD_VECTORS, ids=[v["name"] for v in PAYLOAD_VECTORS])
_reject_ids = pytest.mark.parametrize("v", REJECT_VECTORS, ids=[v["name"] for v in REJECT_VECTORS])


def _backend(v: dict[str, Any]) -> bytes:
    return CryptoService.contract_sign_data(**{k: v[k] for k in KEYS})


def _sdk(v: dict[str, Any]) -> bytes:
    return CDSClient.sign_data(**{k: v[k] for k in KEYS}).encode("utf-8")


def _backend_errors(v: dict[str, Any]) -> list[dict[str, Any]]:
    with pytest.raises(CDSError) as exc:
        _backend(v)
    return exc.value.detail["errors"]


def test_vector_table_is_usable():
    """表被掏空 / 只剩一类，等于把平价测试悄悄变成空转。"""
    assert len(PAYLOAD_VECTORS) >= 5
    assert len(REJECT_VECTORS) >= 8
    assert len({v["name"] for v in VECTORS}) == len(VECTORS)
    for v in VECTORS:
        assert set(v) >= {"name", "op", *KEYS}
    names = " ".join(v["name"] for v in REJECT_VECTORS)
    for klass in ("pipe", "comma", "over", "too-many"):
        assert klass in names, f"越界向量缺类: {klass}"


@_payload_ids
def test_backend_produces_the_bytes_the_table_claims(v):
    """表里写的是后端实产字节——后端一改，这里先红，前端不会独自漂移。"""
    assert _backend(v).decode("utf-8") == v["payload"]


@_payload_ids
def test_sdk_and_backend_produce_identical_bytes(v):
    """A255 平价主体：同一组输入，后端与 SDK 的原文逐字节相同。"""
    assert _sdk(v) == _backend(v)


def _fe_const(name: str) -> int:
    m = re.search(rf"export const {name} = (\d+);", FE_CRYPTO.read_text(encoding="utf-8"))
    assert m, f"前端 crypto.ts 未导出数值常量 {name}，无法与后端比常量"
    return int(m.group(1))


def test_length_limits_are_one_source_across_languages():
    """64 / 16 / 64 只有一处事实：后端类常量、SDK 常量、前端导出常量必须同源。"""
    assert LIMITS == {
        "max_len": CryptoService._PAYLOAD_MAX_LEN,
        "role_max_len": CryptoService._PAYLOAD_ROLE_MAX_LEN,
        "scope_max_items": CryptoService._PAYLOAD_SCOPE_MAX_ITEMS,
    }
    assert {
        "max_len": sdk_client.PAYLOAD_MAX_LEN,
        "role_max_len": sdk_client.PAYLOAD_ROLE_MAX_LEN,
        "scope_max_items": sdk_client.PAYLOAD_SCOPE_MAX_ITEMS,
    } == LIMITS
    assert {
        "max_len": _fe_const("CONTRACT_PAYLOAD_MAX_LEN"),
        "role_max_len": _fe_const("CONTRACT_PAYLOAD_ROLE_MAX_LEN"),
        "scope_max_items": _fe_const("CONTRACT_PAYLOAD_SCOPE_MAX_ITEMS"),
    } == LIMITS


@_reject_ids
def test_backend_refuses_the_vector(v):
    with pytest.raises(CDSError) as exc:
        _backend(v)
    assert exc.value.code == "VALIDATION_ERROR"
    assert exc.value.http_status == 422
    assert exc.value.detail["errors"], "拒签必须带上越界字段明细"


@_reject_ids
def test_sdk_refuses_locally_with_the_same_detail(v):
    """SDK 侧同样在本地拒签（不发请求、不签名），且明细与后端逐字相同。"""
    with pytest.raises(SdkCDSError) as exc:
        _sdk(v)
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "VALIDATION_ERROR"
    assert exc.value.detail["errors"] == _backend_errors(v)


def test_sdk_does_not_validate_what_it_need_not():
    """合法值不得被这条校验误拒：64 字符落库列宽内的合约号照旧可签。"""
    ok = PAYLOAD_VECTORS[-2]
    assert _sdk(ok) == _backend(ok)
