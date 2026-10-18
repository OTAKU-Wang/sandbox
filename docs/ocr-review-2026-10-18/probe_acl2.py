import os, sys
sys.path.insert(0, r"D:/workspace/sandbox")
os.environ.setdefault("TESTING", "1")
os.environ.setdefault("CDS_DATABASE_URL", "sqlite+aiosqlite:///./probe_tmp.db")

from app.services.encrypted_db import EncryptedDatabase
from app.services.field_mask import MaskRule, MaskMode
from app.services.field_acl import FieldACLService

SSN = "110101199001011234"
db = EncryptedDatabase()
db.load_table("users", ["name", "ssn"], [["Alice", SSN], ["Bob", SSN]])
db.create_masked_view("users_safe", "users", [MaskRule("ssn", MaskMode.HASH)])

sql = "SELECT u.name, u.ssn FROM users u JOIN users_safe s ON u.name = s.name"
print("detect ->", db._detect_view_query(sql))
r = db.query(sql)
print("rows:", r.rows)
print("RAW SSN via base-table alias:", SSN in str(r.rows))

sql2 = "SELECT ssn FROM users WHERE name IN (SELECT name FROM users_safe)"
print("\ndetect ->", db._detect_view_query(sql2))
r = db.query(sql2)
print("rows:", r.rows, "raw leak:", SSN in str(r.rows))

# which hash actually runs behind the "sha256" mask pattern
import app.services.crypto_service as cs
from app.services.crypto_service import crypto_service
print("\n_HAS_GMSSL =", cs._HAS_GMSSL)
print("crypto_service.sm3_hash('x') =", crypto_service.sm3_hash(b"x"))
import hashlib
print("sha256('x').hexdigest()      =", hashlib.sha256(b"x").hexdigest())
try:
    import gmssl
    print("gmssl import: OK", getattr(gmssl, "__file__", "?")[:60])
except Exception as e:
    print("gmssl import:", type(e).__name__, e)
from app.core.config import get_settings
s = get_settings()
print("SM3_FALLBACK_ALLOWED default =", getattr(s, "SM3_FALLBACK_ALLOWED", "<missing>"))
print("FieldACLService in CI gate list / callers:", end=" ")
