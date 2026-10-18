
import sys, os
sys.path.insert(0, r'D:/workspace/sandbox')
from app.services.output_inspection import output_inspector as O
from app.services.crypto_service import crypto_service

# force SM2 signing failure -> claim 31
orig_sign = crypto_service.sign
crypto_service.sign = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("hsm down"))
r = O.inspect("hello world", "u1", "sigfail", sandbox_mode="develop")
print("C31 sign-fail -> signature field =", r.signature)
print("C31 stage_results[signature] =", r.stage_results.get("signature"), "passed =", r.passed, "degraded =", r.degraded)
crypto_service.sign = orig_sign

# force sm3_hash to raise inside the except handler -> does it escape as 500?
orig_sm3 = crypto_service.sm3_hash
crypto_service.sign = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("hsm down"))
crypto_service.sm3_hash = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gmssl missing & fallback denied"))
try:
    O.inspect("hello", "u1", "sigfail2", sandbox_mode="develop")
    print("C31b sm3-in-except -> swallowed")
except Exception as e:
    print("C31b sm3-in-except -> ESCAPES", type(e).__name__ + ":", e)
crypto_service.sm3_hash = orig_sm3
crypto_service.sign = orig_sign

# claim 13: keypair is process-random, not loaded from config
kp1 = O._signing_keypair
import app.services.output_inspection as M
M.OutputInspector._signing_keypair = None
r2 = O.inspect("x", "u1", "kp", sandbox_mode="develop")
kp2 = O._signing_keypair
print("C13 regenerated keypair differs:", getattr(kp1,'private_key',None) != getattr(kp2,'private_key',None), "| class attr default:", M.OutputInspector._signing_keypair is not None)
print("C13 sig of same text differs across 'restart':", r.signature != r2.signature)

# claim 27 precise: which strings hit ip_address
import re
pat = O.PATTERNS["ip_address"][0]
for s in ["v1.2.3.4", "server at 1.2.3.4 ok", "999.999.999.999", "elapsed 10.20.30.40ms", "time 12.30.45.6"]:
    print("C27", repr(s), "->", pat.findall(s))
r = O.inspect("connect to 999.999.999.999 failed", "u1", "ip", sandbox_mode="develop")
print("C27 whole-output blocked by a low-severity hit:", r.passed, r.stage_results.get("dlp_scan"),
      [(f.severity, f.type) for f in r.findings if f.stage == "dlp_scan"])

