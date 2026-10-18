"""Micro-probe: S6 substring-ON false accept (post-JOIN section contains 'ON' in an identifier)."""
from app.services.scenario_engine import ScenarioEngine, ScenarioTemplate, OutputColumn

eng = ScenarioEngine()
cases = {
    "commission": "SELECT * FROM a JOIN commission c WHERE a.id = c.id",   # 'COMMISSION' contains 'ON'
    "position":   "SELECT * FROM a JOIN b POSITION(x) WHERE a.id = b.id",  # 'POSITION' contains 'ON'
    "plain":      "SELECT * FROM a JOIN b WHERE a.id = b.id",              # true cartesian
}
for name, sql in cases.items():
    eng.register_template(ScenarioTemplate(
        id=name, name=name, description="d", category="c", parameters=[], sql_template=sql,
        output_schema=[OutputColumn("c", "text", "d")], max_tables=4))
    r = eng.render_sql(name, {})
    print(f"{name:11s} accepted={r.success}  error={r.error!r}  sql={r.sql!r}")
