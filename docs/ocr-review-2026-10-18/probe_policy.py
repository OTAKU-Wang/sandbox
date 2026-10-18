import warnings, asyncio
warnings.simplefilter("always")
from app.services.secure_duckdb import SecureDuckDBEngine

class FakeBundle:
    package_path = "cds/policy/x"
    rego_source = ""
    field_acl = {"ssn": {"read": False}}
    allowed_ops = ["query"]
    sandbox_modes = ["l2c"]
    policy_hash = "deadbeef"
    revoked_at = None

eng = SecureDuckDBEngine("probe-sess")
eng.register_table("users", [{"name": "a", "ssn": "123456789"}])
eng.set_policy(FakeBundle())
try:
    r = eng.execute_query("SELECT * FROM users")
    print("RESULT ok:", r.success, repr(r.error)[:120], r.rows)
except Exception as e:
    print("RAISED:", type(e).__name__, e)
