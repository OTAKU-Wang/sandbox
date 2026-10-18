import sys
sys.path.insert(0, r'D:/workspace/sandbox')
from app.services.rls_engine import RLSPolicy

p = RLSPolicy(policy_id="x", name="n", policy_type="BOGUS")
print("dataclass accepts a non-PolicyType value:", repr(p.policy_type))
print("to_sql_condition ->", repr(p.to_sql_condition({"user_region": "a"})))
