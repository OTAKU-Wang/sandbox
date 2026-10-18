import sys
sys.path.insert(0, r'D:/workspace/sandbox')
from app.services.pg_rls_manager import PGRLSManager

m = PGRLSManager()

print("=== 464 role-name derivation ===")
for sid in ["sess-abcdef123456", "sess-abcdef9999", "----", "", "sess_zzzzzzzzzzzz"]:
    print(f"  {sid!r:26} -> {m._build_role_name(sid)!r}")

print("=== 463/462 get_connection_string ===")
base_full = "postgresql+asyncpg://cds_app:S3cret@db:5432/cds"
print("  base with user   :", m.get_connection_string("sess-abc12345", base_full))
base_nouser = "postgresql+asyncpg://db:5432/cds"
out = m.get_connection_string("sess-abc12345", base_nouser)
print("  base w/o user    :", out)
print("     -> still the privileged app role?", "cds_app" not in out and "sandbox_" not in out)
print("  base with ?params:", m.get_connection_string("sess-abc12345", base_full + "?sslmode=require"))

print("=== 462 options injection ===")
evil = "x y=1 -c row_security=off"
print("  session_id=%r" % evil)
print("   ->", m.get_connection_string(evil, base_full))
evil2 = "a%20-c%20row_security=off"
print("  session_id=%r" % evil2)
print("   ->", m.get_connection_string(evil2, base_full))
print("  from urllib.parse import parse_qsl ->",
      __import__("urllib.parse", fromlist=["parse_qsl"]).parse_qsl(m.get_connection_string(evil, base_full).split("?", 1)[1]))

print("=== 1326/1328 dead admin_dsn / never-set status fields ===")
import inspect
src = inspect.getsource(PGRLSManager)
print("  occurrences of '_admin_dsn' in class source:", src.count("_admin_dsn"))
print("  occurrences of 'error=' (RLSStatus.error setter):", src.count("error="))
print("  get_status(None-ish) for unknown session:", m.get_status("nope"))
