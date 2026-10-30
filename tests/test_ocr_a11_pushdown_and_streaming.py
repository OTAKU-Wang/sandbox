"""A11 同族（OCR 2026-10-18，backend-23c 交回）：整表入内存的两处下推/流式判据。

A11b `app/api/data_products.py::_contracts_referencing_product`
    旧写法 `select(Contract)` 不带谓词，把整张 contracts 读成 ORM 对象集，只为在
    Python 里判断 `product_ids` 是否含某个 product。判据：打到 contracts 的 SELECT
    必须自带 product_ids 谓词，且被物化的 Contract 行数 = 真正引用该产品的行数
    （而不是全表行数）。删除/归档两条守卫的语义必须逐字不变。

A11c `app/api/audit.py::export_audit_records`
    旧写法把窗口内 AuditLog 整段 `.all()` 物化，再在内存里拼完整 CSV，最后
    `StreamingResponse(iter([output.getvalue()]))`——名为流式，实为一个整块。判据：
    响应体必须是多条分批消息，且第一段响应体产生时被物化的 AuditLog 行数必须小于
    全量行数；同时审计面铁律——同一固定数据集下流式字节与「一次性拼装」逐字节相等、
    不丢行、列序与转义不变、窗口外语义不变（retention 0 = 永久，绝不引入 limit）。
"""
import contextlib
import csv
import io
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select

from app.models.audit_log import AuditLog
from app.models.contract import Contract
from app.models.data_product import DataProduct, DataProductStatus
from app.models.user import User, UserRole
from app.services.auth_service import create_access_token, hash_password
from app.main import app
from tests.conftest import test_engine

pytestmark = pytest.mark.asyncio

PRODUCTS_URL = "/api/v1/data-products"
FILLER_CONTRACTS = 25


async def _mk_user(db, role: str, prefix: str):
    unique = uuid.uuid4().hex[:8]
    user = User(
        username=f"{prefix}_{unique}",
        email=f"{prefix}_{unique}@example.com",
        hashed_password=hash_password("testpass123"),
        role=role,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user, {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


async def _mk_product(db, provider_id, status: str = DataProductStatus.DRAFT.value) -> DataProduct:
    product = DataProduct(
        provider_id=provider_id,
        name=f"A11b 产品 {uuid.uuid4().hex[:8]}",
        product_type="structured",
        status=status,
    )
    db.add(product)
    await db.flush()
    return product


async def _mk_contract(db, provider_id, buyer_id, product_ids: list[str], status: str = "active") -> Contract:
    contract = Contract(
        contract_no=f"CT-{uuid.uuid4().hex[:12]}",
        contract_type="data_usage",
        status=status,
        provider_id=provider_id,
        buyer_id=buyer_id,
        title="A11b 测试合约",
        product_ids=list(product_ids),
    )
    db.add(contract)
    await db.flush()
    return contract


class _Probe:
    """一次调用的取证袋：实际执行的 SQL 文本 + 该 ORM 类被物化的行数。

    mapper `load` 事件每行触发一次，但只对「真从 DB 读出」的实例触发，所以调用方
    必须先 `expunge_all()` 清掉 identity map，否则命中缓存的行不计数，会把
    「无界取行」误测成 0。
    """

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.params: list[object] = []
        self.loads: int = 0


@contextlib.contextmanager
def _instrument(model_cls):
    probe = _Probe()
    engine = test_engine.sync_engine

    def _capture_sql(dbapi_conn, cursor, statement, parameters, context, executemany):
        probe.statements.append(statement)
        probe.params.append(parameters)

    def _count_load(instance, context):
        probe.loads += 1

    event.listen(engine, "before_cursor_execute", _capture_sql)
    event.listen(model_cls, "load", _count_load)
    try:
        yield probe
    finally:
        event.remove(engine, "before_cursor_execute", _capture_sql)
        event.remove(model_cls, "load", _count_load)


def _contract_selects(statements: list[str]) -> list[str]:
    return [s for s in statements if "FROM contracts" in s]


_WHERE_RE = re.compile(r"\swhere\s", re.IGNORECASE)


def _where(statement: str) -> str:
    """只取 WHERE 子句：ORM 默认 SELECT 列表里就有 contracts.product_ids，全文包含不能证明谓词存在。"""
    match = _WHERE_RE.search(statement)
    return statement[match.start():] if match else ""


@pytest.mark.asyncio
async def test_a11b_delete_guard_pushes_product_predicate_into_sql(client, db_session):
    """删除守卫：SELECT 必带 product_ids 谓词，且只物化命中行（不是全表行）。"""
    provider, prov_headers = await _mk_user(db_session, UserRole.DATA_PROVIDER, "a11bprov")
    buyer, _ = await _mk_user(db_session, UserRole.BUYER, "a11bbuy")
    product = await _mk_product(db_session, provider.id)
    await _mk_contract(db_session, provider.id, buyer.id, [str(product.id)])
    for _ in range(FILLER_CONTRACTS):
        await _mk_contract(db_session, provider.id, buyer.id, [str(uuid.uuid4())])

    # 清 identity map：让端点那次查询真的走 DB，物化计数才有意义。
    db_session.expunge_all()

    with _instrument(Contract) as state:
        resp = await client.delete(f"{PRODUCTS_URL}/{product.id}", headers=prov_headers)

    assert resp.status_code == 409, resp.text
    selects = _contract_selects(state.statements)
    assert len(selects) == 1, f"expected exactly one contracts SELECT, got {len(selects)}: {selects}"
    # 判据 1：谓词下推到 SQL —— 取行集合本身已经被 product_ids 限定。
    assert "product_ids" in _where(selects[0]), (
        "contracts SELECT 没有把 product_ids 判断下推到 SQL，仍是整表取回后在 Python 里过滤："
        f"{selects[0]}"
    )
    assert any(str(product.id) in str(p) for p in state.params), (
        f"product id 没有作为绑定参数下推：{state.params}"
    )
    # 判据 2：物化行数 = 命中行数（1），不是全表行数（FILLER_CONTRACTS + 1）。
    assert state.loads == 1, (
        f"物化了 {state.loads} 行 Contract，全表 {FILLER_CONTRACTS + 1} 行 —— 无界取行未消除"
    )


@pytest.mark.asyncio
async def test_a11b_archive_guard_semantics_and_pushdown(client, db_session):
    """归档守卫：阻断状态集合语义不变（active 阻断 / completed 不阻断），同样下推。"""
    provider, prov_headers = await _mk_user(db_session, UserRole.DATA_PROVIDER, "a11bprov2")
    buyer, _ = await _mk_user(db_session, UserRole.BUYER, "a11bbuy2")

    blocked = await _mk_product(db_session, provider.id, DataProductStatus.PUBLISHED.value)
    await _mk_contract(db_session, provider.id, buyer.id, [str(blocked.id)], status="active")
    free = await _mk_product(db_session, provider.id, DataProductStatus.PUBLISHED.value)
    await _mk_contract(db_session, provider.id, buyer.id, [str(free.id)], status="completed")
    for _ in range(FILLER_CONTRACTS):
        await _mk_contract(db_session, provider.id, buyer.id, [str(uuid.uuid4())], status="active")
    db_session.expunge_all()

    with _instrument(Contract) as state:
        resp = await client.post(f"{PRODUCTS_URL}/{blocked.id}/archive", headers=prov_headers)
    assert resp.status_code == 409, resp.text
    selects = _contract_selects(state.statements)
    assert len(selects) == 1, selects
    assert "product_ids" in _where(selects[0]), selects[0]
    assert state.loads == 1, (
        f"归档守卫物化了 {state.loads} 行 Contract（全表 {FILLER_CONTRACTS + 2 + 1} 行）"
    )

    resp2 = await client.post(f"{PRODUCTS_URL}/{free.id}/archive", headers=prov_headers)
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["status"] == DataProductStatus.ARCHIVED.value


@pytest.mark.asyncio
async def test_a11b_unreferenced_product_still_deletable(client, db_session):
    """未被任何合约引用的草稿产品仍可删除（下推不得造成假阳性把守卫变成永久 409）。"""
    provider, prov_headers = await _mk_user(db_session, UserRole.DATA_PROVIDER, "a11bprov3")
    buyer, _ = await _mk_user(db_session, UserRole.BUYER, "a11bbuy3")
    product = await _mk_product(db_session, provider.id)
    for _ in range(FILLER_CONTRACTS):
        await _mk_contract(db_session, provider.id, buyer.id, [str(uuid.uuid4())])
    db_session.expunge_all()

    with _instrument(Contract) as state:
        resp = await client.delete(f"{PRODUCTS_URL}/{product.id}", headers=prov_headers)
    assert resp.status_code == 204, resp.text
    assert state.loads == 0, state.loads
    assert all("product_ids" in _where(s) for s in _contract_selects(state.statements)), state.statements

    # conftest 的 get_db override 不提交，所以删除只在会话的待刷新区—— flush 后才能看到行真的没了。
    await db_session.flush()
    assert await db_session.get(DataProduct, product.id) is None


# --- A11c: GET /api/v1/audit/export 必须真分批流式，且字节与一次性拼装逐字节相等 ---

EXPORT_TEST_DAYS = 30
# 与 app.api.audit.EXPORT_CSV_BATCH_ROWS 同值的本地常量：测试只需「数据集 > 一批」，
# 不去钉住生产调优值。固定数据集比一批多几十行，才能同时看到「分批」与「不丢行」。
EXPORT_BATCH_ROWS = 500
AUDIT_ROWS = EXPORT_BATCH_ROWS + 40
# 锚点只取「今日 00:00 UTC」：同一 UTC 日内任何一次运行都得到完全相同的
# created_at 集合，跨运行比对导出字节才有意义（行序也由此严格确定）。
AUDIT_ANCHOR = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
AUDIT_ID_BASE = 0x0A11C0_0000


async def _mk_audit_rows(db_session) -> None:
    """窗口内 AUDIT_ROWS 行（含逗号/引号/换行/空值/unicode，钉住转义与列序）+ 窗口外若干行。"""
    rows = []
    for i in range(AUDIT_ROWS):
        weird = {
            0: 'name,with"comma"',
            1: "多行\n描述\r\n带 CR",
            2: "  前后空格  ",
            3: "密态沙箱 中文 \\",
        }.get(i % 4, f"resource-{i}")
        rows.append(AuditLog(
            id=uuid.UUID(int=AUDIT_ID_BASE + i),
            user_id=None if i % 7 == 0 else uuid.UUID(int=AUDIT_ID_BASE + 10_000),
            session_id=None if i % 5 == 0 else uuid.UUID(int=AUDIT_ID_BASE + 20_000),
            action=f"audit.export_probe.{i % 4}",
            resource_type="data_product" if i % 2 else "sandbox_session",
            resource_id=weird,
            ip_address=None,
            user_agent=None,
            blockchain_tx_hash=None if i % 3 else "0x" + format(i, "064x"),
            created_at=AUDIT_ANCHOR - timedelta(minutes=i),
        ))
    stale = [
        AuditLog(
            id=uuid.UUID(int=AUDIT_ID_BASE + 900_000 + k),
            action="audit.out_of_window",
            resource_type="data_product",
            resource_id=f"stale-{k}",
            created_at=AUDIT_ANCHOR - timedelta(days=200 + k),
        )
        for k in range(3)
    ]
    db_session.add_all(rows)
    db_session.add_all(stale)
    await db_session.commit()


def _reference_export_csv(rows) -> str:
    """独立参考实现：与改前那段一次性拼装同样的列序/空值/isoformat/csv.writer 方言。

    流式实现必须与它逐字节相等——列序、转义、行终止符、行数都不得因流式而漂移。
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["id", "user_id", "session_id", "action", "resource_type", "resource_id", "blockchain_tx_hash", "created_at"])
    for r in rows:
        writer.writerow([
            str(r.id),
            str(r.user_id) if r.user_id else "",
            str(r.session_id) if r.session_id else "",
            r.action,
            r.resource_type,
            r.resource_id or "",
            r.blockchain_tx_hash or "",
            r.created_at.isoformat() if r.created_at else "",
        ])
    return buffer.getvalue()


def _first_diff(a: str, b: str) -> str:
    for idx, (ca, cb) in enumerate(zip(a, b)):
        if ca != cb:
            return f"@{idx}: streamed={ca!r} reference={cb!r}"
    return f"同前缀，长度不同（streamed={len(a)} reference={len(b)}）"


class _BodyProbe:
    """从 ASGI 边界观察响应：非空 body 消息数 + 第一条 body 消息发出时已物化的行数。"""

    def __init__(self) -> None:
        self.messages: int = 0
        self.chunks: list[bytes] = []
        self.loads_at_first_body: int = -1


def _capture_app(inner_app, body_probe: _BodyProbe, loads):
    async def wrapped(scope, receive, send):
        if scope["type"] != "http":
            await inner_app(scope, receive, send)
            return

        async def capture_send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                body_probe.messages += 1
                body_probe.chunks.append(message["body"])
                if body_probe.loads_at_first_body < 0:
                    body_probe.loads_at_first_body = loads()
            await send(message)

        await inner_app(scope, receive, capture_send)

    return wrapped


async def _export(client_app, headers: dict[str, str], days: int, body_probe: _BodyProbe, loads):
    transport = ASGITransport(app=_capture_app(client_app, body_probe, loads))
    async with AsyncClient(transport=transport, base_url="http://testserver") as stream_client:
        return await stream_client.get(
            f"/api/v1/audit/export?format=csv&days={days}", headers=headers
        )


@pytest.mark.asyncio
async def test_a11c_audit_export_is_batch_streaming_and_byte_identical(client, db_session, operator_headers):
    """改前红点：响应构造前把窗口内整段行全取进内存、且只有一个响应体消息。"""
    await _mk_audit_rows(db_session)
    db_session.expunge_all()

    body_probe = _BodyProbe()
    with _instrument(AuditLog) as probe:
        resp = await _export(app, operator_headers, EXPORT_TEST_DAYS, body_probe, lambda: probe.loads)

    assert resp.status_code == 200, resp.text
    streamed = b"".join(body_probe.chunks)
    assert streamed == resp.content, "ASGI 抓到的分块拼接与响应体不一致"

    in_window = (await db_session.execute(
        select(AuditLog)
        .where(AuditLog.created_at >= datetime.now(timezone.utc) - timedelta(days=EXPORT_TEST_DAYS))
        .order_by(AuditLog.created_at)
    )).scalars().all()

    # 审计面铁律：不丢行、列序/转义/行终止符逐字节不变。
    reference = _reference_export_csv(in_window)
    assert streamed.decode("utf-8") == reference, (
        f"字节不一致 streamed={len(streamed)} reference={len(reference)} "
        f"rows streamed={streamed.count(b'\r\n')} reference={reference.count(chr(13) + chr(10))} "
        f"first_diff={_first_diff(streamed.decode('utf-8'), reference)}"
    )
    assert len(in_window) == AUDIT_ROWS
    assert streamed.splitlines()[0] == b"id,user_id,session_id,action,resource_type,resource_id,blockchain_tx_hash,created_at"
    assert streamed.count(b"audit.export_probe.") == AUDIT_ROWS, "导出行数与窗口内行数不一致（不丢行）"

    # 真分批：第一块响应体发出时窗口内行还没被取完（旧写法：响应构造前就全部入内存）。
    assert 0 <= body_probe.loads_at_first_body < AUDIT_ROWS, (
        f"第一块响应体发出时已物化 {body_probe.loads_at_first_body} 行 / 窗口内 {AUDIT_ROWS} 行"
        "——响应构造前就把整段行取进了内存"
    )
    assert body_probe.messages >= 2, f"响应体只有 {body_probe.messages} 块——仍是整块吐出"
    # 逐批取完整个窗口，一行不多一行不少（retention 0 = 永久，不得变成 limit）。
    assert probe.loads == AUDIT_ROWS, probe.loads


@pytest.mark.asyncio
async def test_a11c_export_window_is_a_filter_not_a_limit(client, db_session, operator_headers):
    """窗口外语义不变：days 只按 created_at 过滤，既不截断长尾也不放宽。"""
    await _mk_audit_rows(db_session)
    db_session.expunge_all()

    body_probe = _BodyProbe()
    with _instrument(AuditLog) as probe:
        resp = await _export(app, operator_headers, EXPORT_TEST_DAYS, body_probe, lambda: probe.loads)
    assert resp.status_code == 200, resp.text
    text = resp.text
    stale_ids = [uuid.UUID(int=AUDIT_ID_BASE + 900_000 + k) for k in range(3)]
    for stale in stale_ids:
        assert str(stale) not in text, "窗口外的审计行出现在导出里"
    assert probe.loads == AUDIT_ROWS

    wide = _BodyProbe()
    with _instrument(AuditLog) as probe2:
        resp2 = await _export(app, operator_headers, 210, wide, lambda: probe2.loads)
    assert resp2.status_code == 200, resp2.text
    for stale in stale_ids:
        assert str(stale) in resp2.text, "放宽 days 后窗口外的行应重新可见（过滤而非截断）"
    assert probe2.loads == AUDIT_ROWS + 3


@pytest.mark.asyncio
async def test_a11c_export_json_branch_unchanged(client, db_session, operator_headers):
    """JSON 分支（带 total 的信封）响应形状逐字不变。"""
    await _mk_audit_rows(db_session)
    db_session.expunge_all()
    resp = await client.get(f"/api/v1/audit/export?format=json&days={EXPORT_TEST_DAYS}", headers=operator_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == AUDIT_ROWS
    assert data["period_days"] == EXPORT_TEST_DAYS
    assert len(data["records"]) == AUDIT_ROWS
    assert set(data["records"][0]) == {
        "id", "user_id", "action", "resource_type", "resource_id", "blockchain_tx_hash", "created_at",
    }
