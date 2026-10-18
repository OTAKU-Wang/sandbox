import sys, ast
sys.path.insert(0, r"D:/workspace/sandbox")
from app.services.clickhouse_schema import ClickHouseSchema, ColumnDef, IndexDef
from app.services.clickhouse_schema import clickhouse_schema as chs

def hr(t):
    print("\n" + "=" * 70 + "\nPROBE: " + t)

hr("K1 gateway_metering definition + generated DDL")
t = chs._tables["gateway_metering"]
print("  engine   :", t.engine)
print("  order_by :", t.order_by)
print("  columns  :", [c.name for c in t.columns])
print("  DDL:\n" + chs.generate_create_sql("gateway_metering"))

hr("K2 validate_schema_completeness")
for name in chs.list_tables():
    v = chs.validate_schema_completeness(name)
    print("  %-28s valid=%s column_count=%s missing=%s" % (name, v["valid"], v["column_count"], v["missing_columns"]))
print("  unknown table ->", chs.validate_schema_completeness("nope"))
t.columns.append(ColumnDef("timestamp", "DateTime64(3, 'UTC')"))
t.columns.append(ColumnDef("ghost_col", "UInt8"))
t.order_by = "(timestamp, does_not_exist)"
t.ttl = "missing_col + INTERVAL 3 YEAR"
t.indexes.append(IndexDef("idx_ghost", "ghost_expr", "bloom_filter"))
print("  after duplicate col 'timestamp', dangling order_by/ttl/index refs:")
print("  validate ->", chs.validate_schema_completeness("gateway_metering"))
print("  DDL is now invalid ClickHouse (duplicate ORDER BY key column):")
print(chs.generate_create_sql("gateway_metering")[-400:])

hr("K3 unknown table name indistinguishable")
print("  get_columns('nope')          =", chs.get_columns("nope"))
print("  get_table_info('nope')       =", chs.get_table_info("nope"))
print("  generate_create_sql('nope')  =", repr(chs.generate_create_sql("nope")))
print("  zero-arg exception raised?   : none (silent)")

hr("K4 ColumnDef.comment")
src = open(r"D:/workspace/sandbox/app/services/clickhouse_schema.py", encoding="utf-8").read()
print("  'comment=' occurrences in file :", src.count("comment="))
print("  'col.comment' occurrences      :", src.count("col.comment"))
print("  'COMMENT' in audit_events DDL  :", "COMMENT" in chs.generate_create_sql("audit_events"))
print("  ColumnDef field order          :", list(ColumnDef.__dataclass_fields__))

hr("K5 logger / unused imports")
print("  'logger.' occurrences in file  :", src.count("logger."))
tree = ast.parse(src)
used = set()
for n in ast.walk(tree):
    if isinstance(n, ast.Name):
        used.add(n.id)
    if isinstance(n, (ast.Attribute, ast.Subscript)) and isinstance(n.value, ast.Name):
        used.add(n.value.id)
print("  names referenced:", {c: (c in used) for c in ("Any", "field", "logging", "logger", "dataclass")})

hr("K6 audit_events bloom index exprs vs columns (integrity check would flag?)")
t2 = chs._tables["audit_events"]
cols = {c.name for c in t2.columns}
print("  index exprs not among columns:", [i.expr for i in t2.indexes if i.expr.split("(")[-1].strip(") ") not in cols])
print("  order_by keys not among columns:",
      [p.strip("() ") for p in t2.order_by.split(",") if p.strip("() ") not in cols])
print("  duplicate column names:", [c for c in cols if [x.name for x in t2.columns].count(c) > 1])

print("\nDONE")
