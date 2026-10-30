"""A95c — 出沙箱发放门的字段分级取数必须跨方言成立，且取数失败不得静默放行。

台账：ocr-verified-findings.md A95 家族（查询期/发放期字段门）；本条出处 =
backend-wire-b 轨道的转交（``.gate-scratch/evidence/backend-wire-b.md`` 末段
``NEEDS-OWNER: app/services/output_policy.py:88``）。

红（HEAD 实况，SQLite 方言）：``_field_rules_for_contract`` 用
``DataProduct.id.in_([str(p) for p in product_ids])`` 取数 —— SQLite 的 UUID 绑定要
``.hex``，喂 str 会在绑定期抛 ``AttributeError: 'str' object has no attribute 'hex'``，
而这个异常被函数尾部的宽 ``except Exception`` 吞成 ``None`` ⇒ 发放门把「取数炸了」和
「这份数据没有分级」读成同一件事：level≥4 的 deny_out 字段与 level==3 的 mask 字段
一律原文放行。

绿：
1) SQLite 上沿 contract.product_ids → DataProduct.resource_id → DataResource 真取到
   分级并编译成 mask/deny 规则，由发放门执行（deny 字段整批拒发）；
2) 取数**真失败**（product_ids 不可解析 / 资源行缺失 / DB 抛错）⇒ 政策收紧为不放行
   （0 行 + 空格式白名单 + ≥WARNING 的显式披露与 inspection_rule_set 里的原因），
   网关据此拒绝，调用方也无法把它放宽回去。
"""
import logging
import uuid

import pytest

from app.models.contract import Contract, ContractStatus
from app.models.data_product import DataProduct
from app.models.data_resource import DataResource, ResourceStatus
from app.services.output_gateway import OutputGateway
from app.services.output_policy import (
    build_output_policy,
    clamp_gateway_request,
    enforce_text_output_policy,
)

ROWS = [
    {"region": "east", "diagnosis": "dx-77", "contact": "ext-2001"},
    {"region": "west", "diagnosis": "dx-88", "contact": "ext-2002"},
]


async def _mk_resource(db_session, uid: str, classifications: dict[str, int] | None):
    resource = DataResource(
        provider_id=uuid.UUID(uid),
        name=f"a95c-res-{uuid.uuid4().hex[:8]}",
        status=ResourceStatus.READY.value,
        field_classifications=classifications,
    )
    db_session.add(resource)
    await db_session.flush()
    await db_session.refresh(resource)
    return resource


async def _mk_product(db_session, provider_id: str, resource_id):
    product = DataProduct(
        provider_id=uuid.UUID(provider_id),
        name=f"a95c-prod-{uuid.uuid4().hex[:8]}",
        resource_id=resource_id,
    )
    db_session.add(product)
    await db_session.flush()
    await db_session.refresh(product)
    return product


async def _mk_contract(db_session, provider_id: str, buyer_id: str, product_ids: list[str], **kw):
    contract = Contract(
        contract_no=f"A95C-{uuid.uuid4().hex[:10]}",
        contract_type="data_query",
        provider_id=uuid.UUID(provider_id),
        buyer_id=uuid.UUID(buyer_id),
        title="a95c release gate",
        status=ContractStatus.ACTIVE.value,
        product_ids=product_ids,
        max_output_rows=kw.pop("max_output_rows", 10),
        allowed_output_formats=kw.pop("allowed_output_formats", "csv,json"),
        **kw,
    )
    db_session.add(contract)
    await db_session.flush()
    await db_session.refresh(contract)
    return contract


@pytest.mark.asyncio
async def test_sqlite_resolves_real_field_classifications(db_session, make_user):
    """改后绿①：SQLite 上必须真取到分级（HEAD 上被 AttributeError 吞成「无分级」）。"""
    _, provider = await make_user("data_provider", "a95cp")
    _, buyer = await make_user("buyer", "a95cb")
    resource = await _mk_resource(db_session, provider, {"contact": 3, "diagnosis": 4, "region": 1})
    product = await _mk_product(db_session, provider, resource.id)
    contract = await _mk_contract(db_session, provider, buyer, [str(product.id)])

    policy = await build_output_policy(db_session, contract.id)
    rules = (policy.inspection_rule_set or {}).get("field_classifications")
    assert rules is not None, "SQLite 上字段分级取数失败被吞成「无分级」：发放门拿到空规则"
    assert sorted(rules.get("mask_fields") or []) == ["contact"]
    assert sorted(rules.get("deny_out_fields") or []) == ["diagnosis"]
    assert policy.max_output_rows == 10
    assert policy.allowed_output_formats == ["csv", "json"]


@pytest.mark.asyncio
async def test_resolved_rules_are_enforced_by_the_gate(db_session, make_user):
    """取到的分级必须落到发放门：含 deny 字段的成果整批拒发。"""
    _, provider = await make_user("data_provider", "a95cp")
    _, buyer = await make_user("buyer", "a95cb")
    resource = await _mk_resource(db_session, provider, {"contact": 3, "diagnosis": 4})
    product = await _mk_product(db_session, provider, resource.id)
    contract = await _mk_contract(db_session, provider, buyer, [str(product.id)])

    policy = await build_output_policy(db_session, contract.id)
    result = OutputGateway().process(
        data=[dict(r) for r in ROWS], output_format="csv", policy=policy,
        user_id=buyer, session_id="a95c-session",
    )
    assert result.success is False, f"deny_out 字段 diagnosis 的成果被放行：{result.output_data!r}"
    assert "diagnosis" in (result.error or "")

    mask_only = await _mk_resource(db_session, provider, {"contact": 3})
    mask_product = await _mk_product(db_session, provider, mask_only.id)
    mask_contract = await _mk_contract(db_session, provider, buyer, [str(mask_product.id)])
    masked = OutputGateway().process(
        data=[dict(r) for r in ROWS], output_format="csv",
        policy=await build_output_policy(db_session, mask_contract.id),
        user_id=buyer, session_id="a95c-session",
    )
    assert masked.success is True
    assert "***" in (masked.output_data or ""), masked.output_data


@pytest.mark.asyncio
async def test_unparseable_product_id_quarantines_release(db_session, make_user):
    """取数失败①：product_ids 里有非 UUID ⇒ 不能当作「没有分级」继续放行。"""
    _, provider = await make_user("data_provider", "a95cp")
    _, buyer = await make_user("buyer", "a95cb")
    contract = await _mk_contract(db_session, provider, buyer, ["not-a-uuid"])

    policy = await build_output_policy(db_session, contract.id)
    assert policy.max_output_rows == 0
    assert policy.allowed_output_formats == []
    assert (policy.inspection_rule_set or {}).get("field_classification_error")


@pytest.mark.asyncio
async def test_dangling_resource_reference_quarantines_release(db_session, make_user):
    """取数失败②：产品指向不存在的资源 ⇒ 分级不可知，按不放行处理。"""
    _, provider = await make_user("data_provider", "a95cp")
    _, buyer = await make_user("buyer", "a95cb")
    product = await _mk_product(db_session, provider, uuid.uuid4())
    contract = await _mk_contract(db_session, provider, buyer, [str(product.id)])

    policy = await build_output_policy(db_session, contract.id)
    assert policy.max_output_rows == 0
    assert policy.allowed_output_formats == []
    assert (policy.inspection_rule_set or {}).get("field_classification_error")


@pytest.mark.asyncio
async def test_db_error_quarantines_and_is_disclosed(db_session, make_user, monkeypatch, caplog):
    """取数失败③：真 DB 异常 ⇒ 收紧政策 + 显式披露，不得只留一行被吞掉的返回值。"""
    _, provider = await make_user("data_provider", "a95cp")
    _, buyer = await make_user("buyer", "a95cb")
    resource = await _mk_resource(db_session, provider, {"ssn": 4})
    product = await _mk_product(db_session, provider, resource.id)
    contract = await _mk_contract(db_session, provider, buyer, [str(product.id)])

    from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession

    _orig_get = _AsyncSession.get

    async def _boom(self, entity, ident, *args, **kwargs):
        if entity is DataResource:
            raise RuntimeError("simulated connection loss")
        # 必须在打补丁前绑定真方法：写成 `_AsyncSession.get(...)` 就是调用补丁后的自己，
        # 今天绿只是因为 build_output_policy 恰好只 get(DataResource, ...)。
        return await _orig_get(self, entity, ident, *args, **kwargs)

    monkeypatch.setattr(_AsyncSession, "get", _boom)
    with caplog.at_level(logging.WARNING, logger="app.services.output_policy"):
        policy = await build_output_policy(db_session, contract.id)

    assert policy.max_output_rows == 0, "取数异常后仍给出可用的额度 = 静默 fail-open"
    assert policy.allowed_output_formats == []
    assert (policy.inspection_rule_set or {}).get("field_classification_error")
    assert any("field classification" in r.getMessage().lower() for r in caplog.records), \
        "取数失败必须显式披露（≥WARNING），不能只体现在被吞掉的返回值里"


@pytest.mark.asyncio
async def test_quarantined_policy_refuses_and_cannot_be_widened(db_session, make_user):
    """收紧后的政策在网关与文本两条发放路径上都是拒发，且调用方无法放宽。"""
    _, provider = await make_user("data_provider", "a95cp")
    _, buyer = await make_user("buyer", "a95cb")
    contract = await _mk_contract(db_session, provider, buyer, ["not-a-uuid"])
    policy = await build_output_policy(db_session, contract.id)

    rows, fmts = clamp_gateway_request(9999, ["csv", "json", "parquet"], policy)
    assert rows == 0
    assert fmts == []

    result = OutputGateway().process(
        data=[dict(r) for r in ROWS], output_format="csv", policy=policy,
        user_id=buyer, session_id="a95c-session",
    )
    assert result.success is False
    assert not result.output_data

    text, report = await enforce_text_output_policy(
        db_session, None, "\n".join(f"line{i}" for i in range(5)), {}
    )
    assert text == "\n".join(f"line{i}" for i in range(5))
    assert report["policy"]["max_output_rows"] == 10000


@pytest.mark.asyncio
async def test_no_classification_resource_keeps_contract_policy(db_session, make_user):
    """回归护栏：资源确实没分级 / 产品行不存在时，照常给出合约额度（不误伤）。"""
    _, provider = await make_user("data_provider", "a95cp")
    _, buyer = await make_user("buyer", "a95cb")
    resource = await _mk_resource(db_session, provider, {"name": 1, "age": 2})
    product = await _mk_product(db_session, provider, resource.id)

    contract = await _mk_contract(db_session, provider, buyer, [str(product.id)],
                                  max_output_rows=7, allowed_output_formats="csv")
    policy = await build_output_policy(db_session, contract.id)
    assert policy.max_output_rows == 7
    assert policy.allowed_output_formats == ["csv"]
    assert "field_classification_error" not in (policy.inspection_rule_set or {})

    ghost = await _mk_contract(db_session, provider, buyer, [str(uuid.uuid4())], max_output_rows=5)
    ghost_policy = await build_output_policy(db_session, ghost.id)
    assert ghost_policy.max_output_rows == 5
    assert "field_classification_error" not in (ghost_policy.inspection_rule_set or {})
