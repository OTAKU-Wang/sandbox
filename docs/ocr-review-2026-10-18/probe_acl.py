"""Read-only behavioural probe for app/services/encrypted_db.py + field_acl.py.

No DB/API touched: pure in-process module exercise. Prints observed behaviour.
"""
import os, sys, traceback
sys.path.insert(0, r"D:/workspace/sandbox")
os.environ.setdefault("TESTING", "1")
os.environ.setdefault("CDS_DATABASE_URL", "sqlite+aiosqlite:///./probe_tmp.db")
os.environ.setdefault("CDS_SM3_FALLBACK_ALLOWED", "true")

from app.services.encrypted_db import EncryptedDatabase, HAS_DUCKDB
from app.services.field_mask import MaskRule, MaskMode
from app.services.field_acl import FieldACLService

def h(t): print("\n" + "=" * 8 + " " + t + " " + "=" * 8)

print("HAS_DUCKDB =", HAS_DUCKDB)

SSN = "110101199001011234"

def fresh(views=True):
    db = EncryptedDatabase()
    db.load_table("users", ["name", "ssn", "note"], [
        ["Alice", SSN, "see users_safe_x"],
        ["Bob", SSN, "plain"],
    ])
    if views:
        db.create_masked_view("users_safe", "users", [MaskRule("ssn", MaskMode.HASH)])
    return db

h("F0: base table bypasses the masked view")
db = fresh()
r = db.query("SELECT * FROM users")
print("detect ->", db._detect_view_query("SELECT * FROM users"))
print("columns", r.columns, "masked_columns", r.masked_columns)
print("row0", r.rows[0])
print("RAW SSN LEAKED:", SSN in str(r.rows))
r2 = db.query("SELECT u.name, s.ssn FROM users_safe s JOIN users u ON 1=1")
print("join query detect ->", db._detect_view_query("SELECT u.name, s.ssn FROM users_safe s JOIN users u ON 1=1"))
print("join rows", r2.rows[:1], "raw leak:", SSN in str(r2.rows))

h("F1: case-insensitive substring detection (false hit on string literal)")
db = fresh()
sql = "SELECT name FROM users WHERE note = 'see users_safe_x'"
print("detect ->", db._detect_view_query(sql), " (intended target: base table 'users')")
r = db.query(sql)
print("columns", r.columns, "rows returned:", r.row_count, "masked_columns", r.masked_columns)
print("note col leaked ->", [row for row in r.rows][:2])

h("F1b: overlapping view names, first-match-wins")
db = fresh()
db.create_masked_view("users_safe_v2", "users", [MaskRule("name", MaskMode.REDACT)])
sql = "SELECT name, ssn FROM users_safe_v2"
print("detect ->", db._detect_view_query(sql), "(query asks for users_safe_v2)")
r = db.query(sql)
print("masked_columns reported:", r.masked_columns)
print("rows:", r.rows)
print("=> ssn raw in output:", SSN in str(r.rows))

h("F2: str.replace rewrites string literals too")
db = fresh()
sql = "SELECT name, ssn FROM users_safe WHERE name = 'users_safe'"
print("rewritten ->", sql.replace("users_safe", "_masked_users_safe"))
r = db.query(sql)
print("row_count", r.row_count, "rows", r.rows)
db2 = fresh()
r_ok = db2.query("SELECT name, ssn FROM users_safe")
print("control (no WHERE) row_count", r_ok.row_count)

h("F3: DuckDB masked-query failure -> returns every row of every column")
db = fresh()
sql = "SELECT name FROM users_safe WHERE 1=0"   # would return 0 rows if executed
# force the except-branch by making the temp DDL fail: view over a column set DuckDB rejects
db._views["users_safe"].mask_rules  # keep rules
r = db.query(sql)
print("normal path rows:", r.row_count, "cols", r.columns)
db3 = fresh()
sql_bad = "SELECT name, nonexistent_col FROM users_safe"   # DuckDB error -> except branch
r3 = db3.query(sql_bad)
print("except-branch row_count:", r3.row_count, "cols", r3.columns, "masked", r3.masked_columns)
print("rows", r3.rows)

h("F4: identifier injection via unescaped double quote in DDL")
db = fresh(views=False)
try:
    db.load_table('t", "pwned VARCHAR', ["a"], [["1"]])
    print("load_table accepted weird name; tables:", db.list_tables())
    r = db.query('SELECT * FROM duckdb_tables()')
    print("duckdb tables:", [row[0] if isinstance(row, tuple) else row for row in r.rows][:10])
except Exception as e:
    print("load_table raised:", type(e).__name__, e)
db = fresh(views=False)
try:
    db.load_table("t2", ['a", "pwned VARCHAR'], [["1"]])
    print("cols now:", db.get_table_schema("t2"))
    r = db.query("SELECT * FROM t2")
    print("columns from duckdb:", r.columns, "rows", r.rows)
except Exception as e:
    print("col injection raised:", type(e).__name__, e)
db = fresh(views=False)
try:
    db.load_table("t3", ['z"; CREATE TABLE pwned(i INT); --'], [["1"]])
    r = db.query("SELECT * FROM pwned")
    print("INJECTED TABLE EXISTS:", r.columns)
except Exception as e:
    print("pwned query:", type(e).__name__, str(e)[:120])

h("F5: fallback mode (no DuckDB) returns raw rows")
import app.services.encrypted_db as ed
ed.HAS_DUCKDB = False
dbf = ed.EncryptedDatabase()
dbf._conn = None
dbf.load_table("users", ["name", "ssn"], [["Alice", SSN]])
r = dbf.query("SELECT name FROM users")
print("fallback columns", r.columns, "rows", r.rows, "masked", r.masked_columns)
print("raw leak:", SSN in str(r.rows), "(projection 'name' ignored)")
r = dbf.query("SELECT * FROM users_safe")   # view exists? no -> ValueError
print("no-view query raised:", "see below")

h("F5b: fallback + a view over the table")
dbf.create_masked_view("users_safe", "users", [MaskRule("ssn", MaskMode.HASH)])
r = dbf.query("SELECT name FROM users")   # base table, no duckdb
print("base-table fallback still raw:", SSN in str(r.rows))
r = dbf.query("SELECT name FROM users_safe")
print("view fallback: cols", r.columns, "rows", r.rows, "masked", r.masked_columns)

h("field_acl: patterns")
svc = FieldACLService()
for pat in ["prefix:3", "prefix:", "prefix:abc", "prefix:-1", "prefix:99", "sha256", "SHA256", "Prefix:3", "MASK", "null"]:
    try:
        print(f"  {pat!r:12} -> {svc._mask_value('13812345678', pat)!r}")
    except Exception as e:
        print(f"  {pat!r:12} -> RAISES {type(e).__name__}: {e}")
print("  sha256 len:", len(svc._mask_value("x", "sha256")), "value:", svc._mask_value("x", "sha256"))
from app.services.crypto_service import crypto_service
print("  sm3_hash full:", crypto_service.sm3_hash(b"x"), "len", len(crypto_service.sm3_hash(b"x")))

h("field_acl: aggregate_only in allowed + empty acl + bad rule type")
r = svc.filter_fields(["name", "income"], {"income": {"read": True, "aggregate_only": True}})
print("  allowed:", r.allowed_fields, "aggregate_only:", r.aggregate_only_fields)
print("  empty acl -> allowed:", svc.filter_fields(["a", "id_card"], {}).allowed_fields)
print("  agg check w/ global flag:", svc.validate_aggregate_query(["income"], {"income": {"read": True, "aggregate_only": True}}, True))
print("  build_sql_projection:", svc.build_sql_projection(["name", "income"], {"income": {"read": True, "aggregate_only": True}}))
print("  build_sql_projection injection:", svc.build_sql_projection(['x" FROM users; DROP TABLE t--'], {}))
try:
    svc.parse_rules({"ssn": None})
    print("  parse_rules(None rule): no raise")
except Exception as e:
    print("  parse_rules({'ssn': None}) RAISES", type(e).__name__, e)
try:
    svc.filter_fields(["ssn"], {"ssn": None})
except Exception as e:
    print("  filter_fields w/ None rule RAISES", type(e).__name__, e)
h("done")
