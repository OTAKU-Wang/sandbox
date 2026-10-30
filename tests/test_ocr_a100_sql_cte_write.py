"""A100 — SQL 扫描按分号取首词：CTE 体内的写语句在「只允许 SELECT」档放行

台账: docs/ocr-review-2026-10-18/ocr-verified-findings.md §A100
`_extract_sql_statements` 旧实现按 `;` 切段、每段只取首词当语句类型，于是
`WITH t AS (DELETE FROM users RETURNING *) SELECT * FROM t` 的首词是 `WITH`，
在 STRUCTURED_QUERY（只允许 SELECT/WITH/EXPLAIN）里直接放行；`WITH … DELETE` 的
尾段同理。修法口径：掩掉字符串/注释后收集**一段里出现的全部写语句类型**，
任一类型不在该档白名单即 FATAL；两个档各自的白名单集合不变（modeling 仍可
CREATE/INSERT/DROP/ALTER），`ON CONFLICT DO UPDATE` 不算第二条语句。
"""
from app.services.code_scanner import CodeScanner

scanner = CodeScanner()

WRITE_IN_CTE = {
    "DELETE": "WITH t AS (DELETE FROM users RETURNING *) SELECT * FROM t",
    "UPDATE": "WITH t AS (UPDATE accounts SET bal = 0 RETURNING *) SELECT * FROM t",
    "INSERT": "WITH t AS (INSERT INTO audit(x) VALUES (1) RETURNING *) SELECT * FROM t",
    "DROP": "WITH t AS (DROP TABLE users RETURNING *) SELECT * FROM t",
}


def _codes(sql: str, mode: str) -> list[str]:
    result = scanner.scan(sql, "sql", mode)
    return [] if result.passed else [i.code for i in result.issues]


# ── 绕过面：写语句藏在段内（CTE 体 / 尾段）必须 FATAL ───────────────

def test_write_inside_cte_body_is_blocked_in_query_mode():
    """A100：首词 WITH 不能作为放行理由。"""
    for stmt_type, sql in WRITE_IN_CTE.items():
        codes = _codes(sql, "structured_query")
        assert "SQL_WRITE_IN_CTE" in codes, (stmt_type, codes)


def test_write_after_cte_tail_is_blocked_in_query_mode():
    """`WITH x AS (SELECT 1) DELETE FROM t` 是 WITH 开头的第二条语句，同样拒。"""
    codes = _codes("WITH x AS (SELECT 1) DELETE FROM t", "structured_query")
    assert "SQL_FORBIDDEN_STMT" in codes


def test_write_inside_cte_body_is_blocked_in_modeling_mode():
    """modeling 白名单里没有 DELETE/UPDATE，藏进 CTE 一样拒。"""
    assert "SQL_WRITE_IN_CTE" in _codes(
        "WITH t AS (DELETE FROM users RETURNING *) SELECT * FROM t", "structured_modeling"
    )


def test_multi_statement_segment_is_blocked():
    codes = _codes("SELECT 1; DELETE FROM users", "structured_query")
    assert "SQL_FORBIDDEN_STMT" in codes


# ── 不得误杀：合法查询、modeling 档既有语句集、字符串/注释里的关键字 ──

def test_plain_cte_stays_allowed():
    assert _codes("WITH t AS (SELECT 1) SELECT * FROM t", "structured_query") == []
    assert _codes("WITH t AS (SELECT 1) SELECT * FROM t", "structured_modeling") == []


def test_modeling_statement_set_is_unchanged():
    """modeling 档允许的语句类型必须与修前一致（不得顺手收紧或放宽）。"""
    for sql in (
        "SELECT * FROM t",
        "CREATE TABLE tmp AS SELECT 1",
        "INSERT INTO tmp VALUES (1)",
        "DROP TABLE tmp",
        "ALTER TABLE tmp ADD COLUMN x INT",
        "EXPLAIN SELECT * FROM t",
    ):
        assert _codes(sql, "structured_modeling") == [], sql


def test_modeling_upsert_is_not_a_second_statement():
    """`ON CONFLICT DO UPDATE SET` 是 INSERT 的一部分，不是藏在段里的第二条语句。"""
    assert _codes(
        "INSERT INTO t (id, v) VALUES (1, 2) ON CONFLICT (id) DO UPDATE SET v = 2",
        "structured_modeling",
    ) == []


def test_keyword_in_string_or_comment_is_not_a_statement():
    for sql in (
        "SELECT id FROM t WHERE note = 'delete from users'",
        "SELECT id FROM t -- delete from users\nWHERE id > 0",
        "WITH t AS (SELECT deleted, insert_count FROM audit) SELECT * FROM t",
    ):
        assert _codes(sql, "structured_query") == [], sql


def test_semicolon_inside_string_is_not_a_statement_separator():
    assert _codes("SELECT id FROM t WHERE s = 'a;b'", "structured_query") == []


def test_modes_without_a_statement_whitelist_are_unchanged():
    """非 query/modeling 档本来就只查文件 I/O 与系统目录，不得被本条顺手加白名单。"""
    assert _codes("WITH t AS (SELECT 1) SELECT * FROM t", "product_dev") == []
    assert _codes("DELETE FROM t", "product_dev") == []


# ── 既有闸门不得回退 ───────────────────────────────────────────────

def test_existing_sql_gates_still_fire():
    assert "SQL_FORBIDDEN_STMT" in _codes("INSERT INTO users VALUES (1)", "structured_query")
    assert _codes("DELETE FROM users", "structured_query") != []
    assert "SQL_FILE_IO" in _codes(
        "SELECT * INTO OUTFILE '/tmp/out.csv' FROM users", "structured_query"
    )
    assert _codes("LOAD DATA INFILE '/tmp/d.csv' INTO TABLE users", "structured_query") != []
    assert "SQL_CATALOG_ACCESS" in _codes("SELECT * FROM pg_tables", "structured_query")
    assert _codes("EXPLAIN SELECT * FROM users", "structured_query") == []
