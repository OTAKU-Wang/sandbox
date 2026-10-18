"""D-e2e-15 回归：会话与合约的归属校验不能因列类型不同而恒假。

`sandbox_sessions.contract_id` 落库是 String(255)，`contracts.id` 是 UUID(as_uuid=True)。
真实运行里两侧指向同一份合约，旧代码 `session.contract_id != contract.id` 仍判为不等，
数据集打包对任何会话都返回 403 CONTRACT_MISMATCH（本机 Docker 端到端实测）。
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.deliveries import _ensure_session_contract
from app.core.errors import CDSError


def _session(contract_id):
    return SimpleNamespace(id=uuid4(), contract_id=contract_id)


def _contract():
    cid = uuid4()
    return cid, SimpleNamespace(id=cid)


def test_string_contract_id_matches_uuid_contract_id():
    """会话存 str、合约存 UUID：同一份合约必须放行（修复前此处必抛）。"""
    cid, contract = _contract()
    _ensure_session_contract(_session(str(cid)), contract)


def test_uuid_typed_contract_id_also_matches():
    cid, contract = _contract()
    _ensure_session_contract(_session(cid), contract)


def test_different_contract_still_rejected():
    _, contract = _contract()
    with pytest.raises(CDSError) as exc:
        _ensure_session_contract(_session(str(uuid4())), contract)
    assert exc.value.code == "CONTRACT_MISMATCH"
    assert exc.value.http_status == 403


def test_session_without_contract_id_is_allowed():
    for empty in (None, ""):
        _ensure_session_contract(_session(empty), contract_ := _contract()[1])
