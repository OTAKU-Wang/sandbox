"""A164（high，本轨只做 ops 建表面）— gateway_metering / sandbox_activity_events 在规范 init SQL 里不存在。

台账: docs/ocr-review-2026-10-18/ocr-verified-findings.md §A164

写入方与查询方都是活路径：`gateway_service._write_metering`（`app/services/gateway_service.py:283`）
INSERT `gateway_metering`、`app/api/gateway.py:364` 聚合它；`app/services/sandbox_audit.py:259/:844`
INSERT `sandbox_activity_events`、`app/api/sandbox_sessions.py:1648` 查它。而 ClickHouse 容器
只执行 `scripts/clickhouse-init.sql`（规范 schema，仓内唯一由部署执行的 DDL），该文件此前
只有 5 张表，这两张一张都没有 ⇒ 冷启动环境里写入即 `Table ... doesn't exist`，被 `except` 吞掉
（沙箱侧 `return 0` / 采集侧只 log），计量与沙箱活动审计静默归零。

本文件是 SQL 静态断言（本机没有 ClickHouse 实例 ⇒ 未跑真组件；不做任何"已连上 ClickHouse 验证"的声明）：

1. 规范 SQL 里两张表必须存在；
2. 每张表的 DDL 必须覆盖两处 INSERT 写入的**每一列**、以及两处 SELECT 读取的**每一列**；
3. 列名/类型/默认值、引擎、分区键、ORDER BY、TTL、跳数索引必须与 `app/services/clickhouse_schema.py`
   的注册表逐字一致——运行面（`_ensure_metering_table`）与部署面（本 SQL）此前就是两套真相，
   这正是本条缺陷的成因，禁止再次漂移。
"""
from __future__ import annotations

import re
from pathlib import Path

from app.services.clickhouse_schema import clickhouse_schema

REPO = Path(__file__).resolve().parents[1]
INIT_SQL = REPO / "scripts" / "clickhouse-init.sql"
SQL_TEXT = INIT_SQL.read_text(encoding="utf-8")

TABLES = ("gateway_metering", "sandbox_activity_events")


def _norm(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _table_block(table: str) -> str:
    marker = f"CREATE TABLE IF NOT EXISTS cds_audit.{table}"
    start = SQL_TEXT.find(marker)
    assert start != -1, f"{INIT_SQL} 里没有 {table} 的建表语句"
    open_paren = SQL_TEXT.index("(", start)
    depth = 0
    for pos in range(open_paren, len(SQL_TEXT)):
        char = SQL_TEXT[pos]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                end = SQL_TEXT.index(";", pos)
                return SQL_TEXT[start:end]
    raise AssertionError(f"{table} 的建表语句括号不闭合")


def _ddl_columns(table: str) -> dict[str, tuple[str, str]]:
    """{column: (type, default)} from the canonical SQL block."""
    block = _table_block(table)
    body = block[block.index("(") + 1: block.rindex(")")]
    columns: dict[str, tuple[str, str]] = {}
    for raw in body.splitlines():
        line = raw.split("--")[0].strip().rstrip(",")
        if not line or line.upper().startswith(("INDEX ", "CONSTRAINT ")):
            continue
        head, _, default = line.partition(" DEFAULT ")
        name, _, dtype = head.partition(" ")
        columns[name] = (_norm(dtype), _norm(default))
    return columns


def _ddl_clause(table: str, keyword: str) -> str:
    block = _table_block(table)
    match = re.search(
        rf"{keyword}\s+(.+?)(?=\n(?:PARTITION BY|ORDER BY|TTL|SETTINGS)|;)",
        block,
        re.DOTALL,
    )
    assert match, f"{table} 的 DDL 缺少 {keyword}"
    clause = "\n".join(part.split("--")[0] for part in match.group(1).splitlines())
    return _norm(clause)


def _ddl_indexes(table: str) -> set[str]:
    block = _table_block(table)
    body = block[block.index("(") + 1: block.rindex(")")]
    return {
        _norm(line.split("--")[0].strip().rstrip(","))
        for line in body.splitlines()
        if line.strip().upper().startswith("INDEX ")
    }


def _inserted_columns(table: str) -> set[str]:
    columns: set[str] = set()
    for source in ("app/services/gateway_service.py", "app/services/sandbox_audit.py"):
        text = (REPO / source).read_text(encoding="utf-8")
        for match in re.finditer(
            rf"INSERT INTO {table}\s*\(([^)]*)\)", text, re.DOTALL
        ):
            columns |= {
                part.strip() for part in match.group(1).split(",") if part.strip()
            }
    assert columns, f"没有找到任何写入 {table} 的 INSERT——用例本身失效"
    return columns


QUERY_IGNORE_WORDS = {
    "SELECT", "FROM", "WHERE", "AND", "OR", "ORDER", "BY", "DESC", "ASC", "LIMIT",
    "AS", "COUNT", "SUM", "AVG", "MIN", "MAX", "TODAY", "NOW", "TOSTRING",
    "TOUUID", "TODATE", "DATE", "IF", "NULL", "COALESCE", "INT", "UINT64",
}


def _queried_columns(table: str) -> set[str]:
    columns: set[str] = set()
    for source in ("app/api/gateway.py", "app/api/sandbox_sessions.py"):
        text = (REPO / source).read_text(encoding="utf-8")
        for match in re.finditer(
            rf"SELECT(.+?)FROM {table}", text, re.DOTALL
        ):
            select_list = re.sub(r"\bas\s+\w+", "", match.group(1))
            columns |= {
                token
                for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", select_list)
                if len(token) > 1 and token.upper() not in QUERY_IGNORE_WORDS
            }
    assert columns, f"没有找到任何读取 {table} 的 SELECT——用例本身失效"
    return columns


def test_canonical_init_sql_creates_both_tables():
    for table in TABLES:
        assert f"CREATE TABLE IF NOT EXISTS cds_audit.{table}" in SQL_TEXT, (
            f"{table} 有写入方和查询方，却没有部署执行的建表 DDL"
        )


def test_ddl_covers_every_written_column():
    for table in TABLES:
        ddl = set(_ddl_columns(table))
        written = _inserted_columns(table)
        assert written - ddl == set(), (
            f"INSERT 写了 DDL 里不存在的列（写入必然失败）：{sorted(written - ddl)}"
        )


def test_ddl_covers_every_queried_column():
    for table in TABLES:
        ddl = set(_ddl_columns(table))
        queried = _queried_columns(table)
        assert queried - ddl == set(), (
            f"SELECT 读了 DDL 里不存在的列：{sorted(queried - ddl)}"
        )


def test_init_sql_matches_the_runtime_table_registry():
    """部署面与运行面必须是同一份 schema，否则冷启动与"应用自己建过表"两种环境行为不同。"""
    for table in TABLES:
        registry_sql = clickhouse_schema.generate_create_sql(table)
        assert registry_sql, f"注册表里没有 {table}"
        registry_cols: dict[str, tuple[str, str]] = {}
        registry_body = registry_sql[registry_sql.index("(") + 1: registry_sql.rindex(")")]
        for raw in registry_body.splitlines():
            line = raw.strip().rstrip(",")
            if not line or line.upper().startswith("INDEX "):
                continue
            head, _, default = line.partition(" DEFAULT ")
            name, _, dtype = head.partition(" ")
            registry_cols[name] = (_norm(dtype), _norm(default))
        assert _ddl_columns(table) == registry_cols, (
            f"{table} 的规范 SQL 与 clickhouse_schema 注册表列定义不一致"
        )


def test_init_sql_matches_the_runtime_engine_partition_order_and_ttl():
    for table in TABLES:
        info = clickhouse_schema.get_table_info(table)
        assert info, f"注册表里没有 {table}"
        assert _ddl_clause(table, "ENGINE =") == _norm(info["engine"]), table
        assert _ddl_clause(table, "PARTITION BY") == _norm(info["partition_key"]), table
        assert _ddl_clause(table, "ORDER BY") == _norm(info["order_by"]), table
        assert _ddl_clause(table, "TTL") == _norm(info["ttl"]), table


def test_skip_indexes_are_declared_like_the_registry():
    for table in TABLES:
        registry_sql = clickhouse_schema.generate_create_sql(table)
        registry_body = registry_sql[registry_sql.index("(") + 1: registry_sql.rindex(")")]
        expected = {
            _norm(line.strip().rstrip(","))
            for line in registry_body.splitlines()
            if line.strip().upper().startswith("INDEX ")
        }
        assert _ddl_indexes(table) == expected, table
