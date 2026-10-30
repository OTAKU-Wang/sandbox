"""A164: gateway_metering 有写入有查询但全仓无建表 DDL ⇒ 计量静默归零（high，活路径）。

本文件只验 gateway_service 这一侧的两件事（ClickHouse 初始化 SQL 属 `scripts/`，不在本轨
授权文件内，见 .gate-scratch/evidence/backend-21.md 的 NEEDS-OWNER）：
1. 写入前必须把 DDL 补上（`clickhouse_schema.generate_create_sql` 是仓内已有的真值源，
   产出的就是 `CREATE TABLE IF NOT EXISTS cds_audit.gateway_metering ...`，幂等）；
2. 写不进去（表缺失、后端没配）不能再只落一行 warning：必须计数、可对外读出、并按 ERROR
   级别暴露，否则 /api/v1/gateway 的计量接口会永远报 0 而没有任何信号。
"""
import logging

import pytest

from app.services.gateway_service import GatewayService, MeteringRecord


class _FakeCH:
    """最小 ClickHouse 客户端替身：记录语句，按脚本决定成败。"""

    def __init__(self, fail_on=None):
        self.statements: list[str] = []
        self.rows: list[list] = []
        self._fail_on = fail_on

    def execute(self, sql, *args, **kwargs):
        self.statements.append(sql)
        if self._fail_on and self._fail_on in sql:
            raise RuntimeError("Table cds_audit.gateway_metering doesn't exist")
        if sql.lstrip().upper().startswith("INSERT"):
            self.rows.extend(*args)


@pytest.fixture()
def svc():
    service = GatewayService()
    yield service
    service.reset_metering_health()


def _record():
    return MeteringRecord(request_id="req-1", contract_id="c-1", app_id="app-1",
                          operation="query", rows_returned=7, bytes_returned=42)


def test_metering_ensures_table_before_first_insert(svc):
    """DDL 从没执行过是这条 finding 的本体：写入前必须先补建表。"""
    fake = _FakeCH()
    svc._clickhouse_client = fake

    svc.record_metering(_record())

    create = [s for s in fake.statements if s.lstrip().upper().startswith("CREATE TABLE")]
    assert create, f"没有任何建表语句被发出，statements={fake.statements}"
    assert "gateway_metering" in create[0]
    assert "IF NOT EXISTS" in create[0]
    assert fake.statements.index(create[0]) < next(
        i for i, s in enumerate(fake.statements) if s.lstrip().upper().startswith("INSERT")
    )
    assert svc.metering_health()["written"] == 1
    assert svc.metering_health()["table_ready"] is True


def test_metering_ddl_is_issued_once(svc):
    fake = _FakeCH()
    svc._clickhouse_client = fake

    svc.record_metering(_record())
    svc.record_metering(_record())

    assert sum(1 for s in fake.statements if s.lstrip().upper().startswith("CREATE TABLE")) == 1
    assert svc.metering_health()["written"] == 2


def test_metering_insert_failure_is_visible(svc, caplog):
    """写失败必须计数 + ERROR（原来是 warning 且被吞，接口静默报 0）。"""
    fake = _FakeCH(fail_on="INSERT")
    fake.execute = _ddl_ok_then_fail(fake)
    svc._clickhouse_client = fake

    with caplog.at_level(logging.ERROR):
        svc.record_metering(_record())

    health = svc.metering_health()
    assert health["failed"] == 1
    assert health["written"] == 0
    assert "gateway_metering" in (health["last_error"] or "")
    assert any(r.levelno >= logging.ERROR and "etering" in r.getMessage() for r in caplog.records)


def _ddl_ok_then_fail(fake):
    """DDL 可执行、INSERT 仍失败——缺表且账号无 DDL 权限时的真实形态。"""

    def _execute(sql, *args, **kwargs):
        fake.statements.append(sql)
        if sql.lstrip().upper().startswith("INSERT"):
            raise RuntimeError("Table cds_audit.gateway_metering doesn't exist")

    return _execute


def test_metering_without_backend_is_counted_not_dropped(svc, caplog):
    """没配 ClickHouse 时原来是 `if not client: return` —— 计量归零且零信号。"""
    svc._get_clickhouse = lambda: None

    with caplog.at_level(logging.ERROR):
        svc.record_metering(_record())

    health = svc.metering_health()
    assert health["backend"] == "unavailable"
    assert health["dropped"] == 1
    assert any(r.levelno >= logging.ERROR and "etering" in r.getMessage() for r in caplog.records)
