"""Read-only runtime probes for app/services/remote_attestation.py claims."""
import json
import traceback
from datetime import datetime, timezone

from app.services import remote_attestation as ra
from app.services.remote_attestation import (
    AttestationService, AttestationPolicy, SGXProvider, SEVProvider,
    FirecrackerProvider, TEEQuote, TEEType, QuoteType, AttestationStatus,
    _LOCAL_ATTESTATION_ROOT, _sign_quote_payload,
)

def hr(t):
    print("\n===== " + t)

# --- C5/C6/C7: non-dict JSON payload -----------------------------
hr("C5/C6/C7 non-dict JSON")
for prov, tt in ((SGXProvider(), TEEType.SGX), (SEVProvider(), TEEType.SEV_SNP), (FirecrackerProvider(), TEEType.FIRECRACKER)):
    for blob in (b"[1,2]", b'"str"', b"null", b"123"):
        q = TEEQuote(quote_id="q1", quote_type=QuoteType.SOFTWARE_HASH, tee_type=tt,
                     raw_bytes=blob, measurement="m", report_data="")
        try:
            r = prov.verify_quote(q)
            print(f"{tt.value:12} {blob!r:8} -> status={r.status.value}")
        except Exception as e:
            print(f"{tt.value:12} {blob!r:8} -> RAISED {type(e).__name__}: {e}")

hr("C5/C6/C7 through AttestationService.verify_quote (service level)")
svc = AttestationService()
q = TEEQuote(quote_id="q1", quote_type=QuoteType.SOFTWARE_HASH, tee_type=TEEType.FIRECRACKER,
             raw_bytes=b"[1,2]", measurement="m", report_data="")
try:
    print(svc.verify_quote(q).status.value)
except Exception as e:
    print("RAISED", type(e).__name__, e)
print("log size after exception:", len(svc._verification_log))

# --- C16 limit edge cases ---------------------------------------
hr("C16 get_verification_log limit edges")
svc2 = AttestationService()
for _ in range(5):
    svc2.verify_quote(svc2.generate_quote(TEEType.SGX))
print("limit=0  ->", len(svc2.get_verification_log(limit=0)))
print("limit=-2 ->", len(svc2.get_verification_log(limit=-2)))
print("limit=3  ->", len(svc2.get_verification_log(limit=3)))

# --- C3 naive timestamp / future timestamp ----------------------
hr("C3 naive + future timestamp")
q = svc2.generate_quote(TEEType.SGX)
q.timestamp = datetime(2020, 1, 1)  # naive
try:
    print(svc2.verify_quote(q).status.value)
except Exception as e:
    print("RAISED", type(e).__name__, e)
q2 = svc2.generate_quote(TEEType.SGX)
q2.timestamp = datetime.now(timezone.utc).replace(year=2999)  # far future
r = svc2.verify_quote(q2)
print("future timestamp ->", r.status.value, "verified:", r.verified)
# replay: old signed quote, fresh timestamp field
old = svc2.generate_quote(TEEType.SGX)
raw = old.raw_bytes
old.timestamp = datetime(2001, 1, 1, tzinfo=timezone.utc)
print("old ts ->", svc2.verify_quote(old).status.value)
replay = TEEQuote(quote_id=old.quote_id, quote_type=old.quote_type, tee_type=old.tee_type,
                  raw_bytes=raw, measurement=old.measurement, report_data=old.report_data,
                  timestamp=datetime.now(timezone.utc))
r = svc2.verify_quote(replay)
print("replayed with fresh ts ->", r.status.value, "verified:", r.verified)

# --- C3/C1 report_data nonce binding + offline forgery ----------
hr("C1/C3 offline forgery with public constant")
payload = {"quote_id": "sgx-attacker0001", "type": "sgx_ecdsa",
           "measurement": __import__("app.utils.crypto", fromlist=["sm3_hash"]).sm3_hash(b"sgx-enclave-sgx-attacker0001"),
           "report_data": "aa" * 32, "tcb_version": "2.23", "firmware": "SGX 2.0"}
payload["signature"] = _sign_quote_payload(payload)
forged = TEEQuote(quote_id=payload["quote_id"], quote_type=QuoteType.SGX_ECDSA, tee_type=TEEType.SGX,
                  raw_bytes=json.dumps(payload, sort_keys=True).encode(),
                  measurement=payload["measurement"], report_data=payload["report_data"])
r = AttestationService().verify_quote(forged)
print("forged verified:", r.verified, r.status.value, "trust_root:", r.trust_root)
# does verify check report_data against TEEQuote field?
p2 = json.loads(forged.raw_bytes)
p2["report_data"] = "bb" * 32
p2["signature"] = _sign_quote_payload(p2)
forged.raw_bytes = json.dumps(p2, sort_keys=True).encode()
forged.report_data = "cc" * 32
print("mismatched report_data verified:", AttestationService().verify_quote(forged).verified)

# --- C8 measurement derived from random quote_id ----------------
hr("C8 measurement derivation")
fc = FirecrackerProvider()
a, b = fc.generate_quote(), fc.generate_quote()
print("same components, different measurement:", a.measurement[:16], b.measurement[:16], a.measurement != b.measurement)
sgx = SGXProvider()
print("sgx m1!=m2:", sgx.generate_quote().measurement != sgx.generate_quote().measurement)
wl = AttestationPolicy(allowed_measurements={"a" * 64})
print("whitelist reject:", AttestationService(policy=wl).verify_quote(sgx.generate_quote()).status.value)

# --- C13 policy_checks on failure -------------------------------
hr("C13 policy_checks on a failed verification")
svc3 = AttestationService()
bad = svc3.generate_quote(TEEType.SGX)
p = json.loads(bad.raw_bytes)
p["report_data"] = "ff" * 32          # keeps JSON valid, breaks signature
bad.raw_bytes = json.dumps(p, sort_keys=True).encode()
r = svc3.verify_quote(bad)
print("status:", r.status.value, "verified:", r.verified)
print("policy_checks:", r.policy_checks)

# --- C11 log growth on the module singleton ---------------------
hr("C11 singleton log")
print("singleton log len at import:", len(ra.attestation_service._verification_log),
      "type:", type(ra.attestation_service._verification_log).__name__, "maxlen:",
      getattr(ra.attestation_service._verification_log, "maxlen", "n/a"))

# --- C9 min_tcb_version / require_signed unused -----------------
hr("C9 policy field usage")
import inspect
src = inspect.getsource(ra)
for name in ("min_tcb_version", "require_signed"):
    uses = [i + 1 for i, line in enumerate(src.splitlines()) if name in line]
    print(name, "->", uses)
print("docstring mentions TCB:", "TCB version check" in src)
print("tcb_status literals:", sorted({l.strip() for l in src.splitlines() if "tcb_status=" in l}))

# --- C12 crypto_service usage -----------------------------------
hr("C12 crypto_service usage")
print("occurrences of crypto_service:", [i + 1 for i, l in enumerate(src.splitlines()) if "crypto_service" in l])

# --- C10 register_provider trust-root labelling -----------------
hr("C10 register_provider overrides built-in")
class RealSGX:
    tee_type = TEEType.SGX
    def generate_quote(self, report_data=b""):
        return SGXProvider().generate_quote(report_data)
    def verify_quote(self, quote):
        from app.services.remote_attestation import AttestationResult
        return AttestationResult(quote_id=quote.quote_id, status=AttestationStatus.VERIFIED,
                                 tee_type=TEEType.SGX, measurement=quote.measurement, verified=True)
s4 = AttestationService()
s4.register_provider(RealSGX())
r = s4.verify_quote(s4.generate_quote(TEEType.SGX))
print("custom SGX provider trust_root:", r.trust_root)
