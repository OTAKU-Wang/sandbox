import sys
sys.path.insert(0, r'D:/workspace/sandbox')
from app.services.rls_engine import RLSEngine, RLSPolicy, RLSContext, PolicyType, SensitivityLevel


def fixed_evaluate(self, policy, row, user_region=None, user_clearance=None):
    """Proposed minimal fix for claims 517 / 518 / 519."""
    t = policy.get("type", "")
    if t == "region":
        allowed = policy.get("allowed_regions", [])
        if user_region is None or user_region not in allowed:
            return False
        r = row.get("region")
        return bool(r) and r == user_region
    if t == "sensitivity":
        if user_clearance is None:
            return False
        effective = min(int(user_clearance), int(policy.get("max_level", 0)))
        return int(row.get("sensitivity", 0)) <= effective
    if t == "time":
        return self.evaluate_time_policy(policy)
    return False


RLSEngine.evaluate = fixed_evaluate

cases = [
    ("test_region_allow", True, "region", {"type": "region", "allowed_regions": ["cn-east", "cn-north"]}, {"region": "cn-east", "data": "t"}, "cn-east"),
    ("test_region_deny", False, "region", {"type": "region", "allowed_regions": ["cn-east", "cn-north"]}, {"region": "us-west", "data": "t"}, "us-west"),
    ("test_region_no_user_context", False, "region", {"type": "region", "allowed_regions": ["cn-east"]}, {"region": "cn-east"}, None),
    ("test_composite_all_pass", True, "sensitivity", {"type": "sensitivity", "max_level": 3}, {"sensitivity": 2}, 3),
    ("test_sensitivity_allowed", True, "sensitivity", {"type": "sensitivity", "max_level": 3}, {"sensitivity": 2}, 3),
    ("test_sensitivity_denied", False, "sensitivity", {"type": "sensitivity", "max_level": 3}, {"sensitivity": 3}, 1),
    ("test_sensitivity_exact_match", True, "sensitivity", {"type": "sensitivity", "max_level": 3}, {"sensitivity": 3}, 3),
]
e = RLSEngine()
ok = True
for name, exp, t, pol, row, uc in cases:
    got = e.evaluate(pol, row, user_region=uc if t == "region" else None,
                     user_clearance=uc if t == "sensitivity" else None)
    ok &= got is exp
    print(f"  {'PASS' if got is exp else 'FAIL'} {name}: got {got} expected {exp}")
print("fixed evaluate() keeps every pinned assertion:", ok)
print("  unknown policy_type now denies:", e.evaluate({"type": "regio"}, {"region": "x"}, user_region="x"))
print("  cross-region row now denies:",
      e.evaluate({"type": "region", "allowed_regions": ["cn-north", "cn-east"]}, {"region": "cn-east"}, user_region="cn-north"))
print("  composite still denies mismatched region:",
      e.evaluate_composite([{"type": "region", "allowed_regions": ["cn-east"]}, {"type": "sensitivity", "max_level": 3}],
                           {"region": "us-west", "sensitivity": 2}, user_region="cn-east", user_clearance=3))


def fixed_sens(self, ctx):
    """Proposed minimal fix for claim 511."""
    col = self.conditions.get("sensitivity_column", "security_level")
    max_rank = SensitivityLevel.clearance_rank(self.conditions.get("max_allowed_level", "public"))
    user_rank = SensitivityLevel.clearance_rank(ctx.get("user_clearance") or "public")
    if max_rank < 0 or user_rank < 0:
        return "FALSE"
    eff = min(user_rank, max_rank)
    lv = [l.value for l in SensitivityLevel if SensitivityLevel.clearance_rank(l.value) <= eff]
    if not lv:
        return "FALSE"
    return f"{col} IN (" + ", ".join("'" + v + "'" for v in lv) + ")"


RLSPolicy._sensitivity_condition = fixed_sens
eng = RLSEngine()
eng.add_policy(RLSPolicy("p", "n", PolicyType.SENSITIVITY,
                         conditions={"max_allowed_level": "confidential", "sensitivity_column": "security_level"}))
print("  fixed secret  ->", repr(eng.evaluate_sql(RLSContext(user_clearance="secret"))),
      "(tests/test_sprint4_services.py:246 asserts None -> behaviour change, test must be updated)")
print("  fixed public  ->", repr(eng.evaluate_sql(RLSContext(user_clearance="public"))))
print("  fixed None    ->", repr(eng.evaluate_sql(RLSContext())), "(no `IN ()`)")
