"""Runtime probe: scenario_engine claims S1..S9 (pure python, no DB, no net)."""
from app.services.scenario_engine import (
    ScenarioEngine, ScenarioTemplate, ScenarioParameter, OutputColumn,
)

OUT = []
def p(*a):
    OUT.append(" ".join(str(x) for x in a))

def mk(tid, sql, params=None, **kw):
    t = ScenarioTemplate(
        id=tid, name=tid, description="d", category="c",
        parameters=params or [], sql_template=sql,
        output_schema=[OutputColumn("c", "text", "d")], **kw)
    eng.register_template(t)
    return t

eng = ScenarioEngine()

# --- S1: numeric context injection (no quotes around placeholder) -----------
mk("num", "SELECT * FROM orders WHERE id = {user_id}")
r = eng.render_sql("num", {"user_id": "1 OR 1=1"})
p("S1a numeric '1 OR 1=1' ->", r.success, repr(r.sql))
r = eng.render_sql("num", {"user_id": "1 UNION SELECT a,b,c FROM secrets"})
p("S1b numeric UNION     ->", r.success, repr(r.sql))

# --- S1: string context, blacklist gaps (OR / UNION / SELECT / sleep) -------
mk("str", "SELECT * FROM t WHERE name = '{q}'")
for inj in ["x' OR '1'='1", "x' UNION SELECT col FROM information_schema.tables --",
            "x'; SELECT pg_sleep(10) --"]:
    r = eng.render_sql("str", {"q": inj})
    p("S1c str inj", repr(inj), "->", r.success, repr(r.sql))

# --- S2: comment reconstitution BEFORE/AFTER keyword strip ------------------
for bad in ["DR--OP TABLE t", "UN/*x*/ION SELECT", "DROP/*x*/"]:
    r = eng.render_sql("str", {"q": bad})
    p("S2 comment-reassembly", repr(bad), "-> rendered:", repr(r.sql))

# --- S2b: silent corruption of legitimate business data --------------------
for good in ["delete user report", "updated_at filter", "A;B Corp", "it's fine"]:
    r = eng.render_sql("str", {"q": good})
    p("S2b silent rewrite", repr(good), "->", repr(r.sql))

# --- S3: LIMIT enforcement --------------------------------------------------
mk("biglimit", "SELECT * FROM t WHERE x='{q}' LIMIT 1000000",
   [ScenarioParameter("q", "string", "q")], max_output_rows=500)
r = eng.render_sql("biglimit", {"q": "a"})
p("S3a template LIMIT 1000000 kept?", r.success, repr(r.sql))
mk("collimit", "SELECT delivery_limit FROM t WHERE x='{q}'",
   [ScenarioParameter("q", "string", "q")], max_output_rows=500)
r = eng.render_sql("collimit", {"q": "a"})
p("S3b column named delivery_limit -> no LIMIT appended?", r.success, repr(r.sql))
mk("paramlimit", "SELECT * FROM t WHERE note='{q}'",
   [ScenarioParameter("q", "string", "q")], max_output_rows=500)
r = eng.render_sql("paramlimit", {"q": "see the limit docs"})
p("S3c param value contains 'limit' ->", r.success, repr(r.sql))
mk("semi", "SELECT * FROM t WHERE x='{q}'", [ScenarioParameter("q","string","q")])
p("S3d semicolon stripped from param:", repr(eng.render_sql("semi", {"q": "a;b"}).sql))

# --- S4: forbidden_operations ----------------------------------------------
mk("subq", "SELECT a FROM t WHERE b = (SELECT max(c) FROM u)", max_tables=4)
t = eng.get_template("subq")
t.forbidden_operations = ["subquery_leak"]
r = eng.render_sql("subq", {})
p("S4a subquery_leak hit but still success?", r.success, repr(r.sql))
mk("subq2", "SELECT (SELECT secret FROM u) AS x FROM t")
eng.get_template("subq2").forbidden_operations = ["subquery_leak"]
p("S4b select-list subquery detected?", eng.render_sql("subq2", {}).success)
mk("net", "SELECT * FROM t WHERE x = pg_read_file('/etc/passwd')")
eng.get_template("net").forbidden_operations = ["network_call"]
p("S4c pg_read_file blocked?", eng.render_sql("net", {}).success)
mk("net2", "SELECT * FROM t WHERE x = UTL_HTTP.REQUEST('u')")
eng.get_template("net2").forbidden_operations = ["network_call"]
p("S4d UTL_HTTP blocked?", eng.render_sql("net2", {}).success)
mk("exp", "SELECT * FROM t WHERE x='{q}'", [ScenarioParameter("q","string","q")])
eng.get_template("exp").forbidden_operations = ["row_level_export"]
p("S4e row_level_export handled?", eng.render_sql("exp", {"q": "z"}).success)

# --- S5: table count via comma joins ---------------------------------------
mk("comma", "SELECT * FROM a, b, c, d, e, f WHERE a.id=b.id", max_tables=4)
p("S5a six comma tables, max_tables=4 ->", eng.render_sql("comma", {}).success,
  eng.render_sql("comma", {}).error)
mk("union", "SELECT a FROM t1 UNION SELECT b FROM t2 UNION SELECT c FROM t3", max_tables=2)
p("S5b union of 3 tables, max_tables=2 ->", eng.render_sql("union", {}).success)

# --- S6: ON substring check -------------------------------------------------
mk("json", "SELECT json_col FROM a JOIN b ON a.id=b.id", max_tables=4)
p("S6a normal ON ok?", eng.render_sql("json", {}).success, eng.render_sql("json", {}).error)
mk("natural", "SELECT * FROM a NATURAL JOIN b", max_tables=4)
p("S6b NATURAL JOIN rejected?", not eng.render_sql("natural", {}).success,
  eng.render_sql("natural", {}).error)
mk("using", "SELECT * FROM a JOIN b USING (id)", max_tables=4)
p("S6c USING rejected?", not eng.render_sql("success" and "using", {}).success,
  eng.render_sql("using", {}).error)
mk("cart", "SELECT * FROM a JOIN b WHERE a.id > 0", max_tables=4)
p("S6d cartesian (no ON) caught?", not eng.render_sql("cart", {}).success)
mk("version", "SELECT * FROM a JOIN b version ON 1=1", max_tables=4)
p("S6e 'ON 1=1' trivially true accepted?", eng.render_sql("version", {}).success)

# --- S7: caller dict mutated ------------------------------------------------
mk("mut", "SELECT * FROM t WHERE x='{q}' LIMIT 5",
   [ScenarioParameter("q", "string", "q", default="dflt"),
    ScenarioParameter("n", "integer", "n", min_value=10)], max_tables=4)
caller = {"n": "7"}
ok, errs = eng.validate_parameters("mut", caller)
p("S7 is_valid:", ok, "errors:", errs, "| caller dict AFTER validate:", caller)

# --- S8: allowed_values vs str(value) --------------------------------------
mk("av", "SELECT * FROM t WHERE flag = {flag} LIMIT 5",
   [ScenarioParameter("flag", "boolean", "f", allowed_values=["true", "false"])], max_tables=4)
p("S8a bool True vs ['true','false'] ->", eng.validate_parameters("av", {"flag": True}))
mk("av2", "SELECT * FROM t WHERE n = {n} LIMIT 5",
   [ScenarioParameter("n", "number", "n", allowed_values=["1", "2"])], max_tables=4)
p("S8b number 1 -> float 1.0 vs ['1','2'] ->", eng.validate_parameters("av2", {"n": 1}))
mk("av3", "SELECT * FROM t WHERE s = '{s}' LIMIT 5",
   [ScenarioParameter("s", "string", "s", allowed_values=["Active"])], max_tables=4)
p("S8c 'active' vs ['Active'] ->", eng.validate_parameters("av3", {"s": "active"}))
p("S8d list value coerced by str() ->", eng.validate_parameters("av3", {"s": ["Active"]}))

# --- S9: format exceptions beyond KeyError ---------------------------------
mk("idx", "SELECT * FROM t WHERE x = {0} LIMIT 5", max_tables=4)
try:
    r = eng.render_sql("idx", {})
    p("S9a positional placeholder ->", r.success, r.error)
except Exception as e:
    p("S9a positional placeholder RAISED:", type(e).__name__, e)
mk("brace", "SELECT * FROM t WHERE x = '{q' LIMIT 5", max_tables=4)
try:
    r = eng.render_sql("brace", {})
    p("S9b malformed brace ->", r.success, r.error)
except Exception as e:
    p("S9b malformed brace RAISED:", type(e).__name__, e)
mk("attr", "SELECT '{q.__class__.__name__}' AS x LIMIT 5", max_tables=4)
try:
    r = eng.render_sql("attr", {"q": "v"})
    p("S9c attribute probe in template ->", r.success, repr(r.sql), repr(r.error))
except Exception as e:
    p("S9c attribute probe RAISED:", type(e).__name__, e)

# --- S10: dead fields -------------------------------------------------------
mk("dead", "SELECT 1 AS c LIMIT 5")
t = eng.get_template("dead")
t.allowed_operations = []          # declare nothing allowed
r = eng.render_sql("dead", {})
p("S10a allowed_operations=[] still renders?", r.success, "row_count=", r.row_count, "elapsed_ms=", r.elapsed_ms)

# --- S11: silent template overwrite ----------------------------------------
mk("dup", "SELECT 1 AS c LIMIT 5")
mk("dup", "SELECT 2 AS c LIMIT 5")
p("S11 duplicate id overwrite -> sql now:", eng.get_template("dup").sql_template)

print("\n".join(OUT))
