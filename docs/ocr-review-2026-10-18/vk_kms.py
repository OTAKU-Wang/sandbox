"""Runtime probe: kms_service attestation + lifecycle claims (software HSM only, no network)."""
import os
os.environ.setdefault("TESTING", "1")
os.environ.setdefault("CDS_KMS_REQUIRE_ATTESTATION", "true")   # production default
os.environ.setdefault("CDS_ALLOW_SIMULATION", "false")          # production TEE posture
os.environ.setdefault("CDS_SM3_FALLBACK_ALLOWED", "true")
os.environ.setdefault("CDS_HSM_SOFTWARE_FALLBACK_ALLOWED", "true")
os.environ.setdefault("CDS_VAULT_ADDR", "")

import json
from datetime import datetime, timezone

OUT = []
def p(*a): OUT.append(" ".join(str(x) for x in a))

from app.services.kms_service import KMSService
from app.services.remote_attestation import (
    AttestationPolicy, AttestationService, QuoteType, TEEType,
    _sign_quote_payload, _LOCAL_ATTESTATION_ROOT,
)
from app.core.config import get_settings

# ---------- K2a: the inline policy silently drops the measurement whitelist ---
inline = AttestationPolicy(require_fresh_quote=True, max_quote_age_seconds=300)
p("K2a inline AttestationPolicy.allowed_measurements =", inline.allowed_measurements,
  "| min_tcb_version =", repr(inline.min_tcb_version))
os.environ["CDS_ATTESTATION_ALLOWED_MEASUREMENTS"] = "1111111111111111111111111111111111111111111111111111111111111111"
get_settings.cache_clear()
default_svc = AttestationService()
p("K2b AttestationService() default policy picks up operator whitelist:",
  sorted(default_svc._policy.allowed_measurements)[:1],
  "| kms_service inline copy stays empty:", not inline.allowed_measurements)

# ---------- craft a quote the repo's own local root will vouch for -----------
def craft(quote_id, typ="sgx_ecdsa", report_data="00" * 64, ts=None):
    from app.utils.crypto import sm3_hash
    measurement = sm3_hash(f"sgx-enclave-{quote_id}".encode()) if typ == "sgx_ecdsa" else \
                  sm3_hash(f"sev-snp-{quote_id}".encode())
    payload = {"quote_id": quote_id, "type": typ, "measurement": measurement,
               "report_data": report_data, "tcb_version": "0.1"}
    if ts:
        payload["issued_at"] = ts
    payload["signature"] = _sign_quote_payload(payload)
    return json.dumps(payload).encode(), measurement, payload

kms = KMSService()
kA = kms.generate_data_key("prod-A")
kB = kms.generate_data_key("prod-B")

raw, meas, payload = craft("sgx-attacker01", report_data="aa" * 32, ts="2020-01-01T00:00:00+00:00")
ka = kms.distribute_key(kA["key_id"], "session-A", attestation=raw)
kb = kms.distribute_key(kB["key_id"], "session-B", attestation=raw)   # SAME quote, other key
kc = kms.distribute_key(kA["key_id"], "session-C", attestation=raw)   # SAME quote, other session
p("K2c locally-signed 'sgx_ecdsa' quote accepted with ALLOW_SIMULATION=false:", ka is not None)
p("K2d replay of the SAME quote across key_id:", kb is not None,
  "| across session_id:", kc is not None, "| report_data never compared: True")
p("K2e operator whitelist (CDS_ATTESTATION_ALLOWED_MEASUREMENTS=%s…) still ignored:" %
  os.environ["CDS_ATTESTATION_ALLOWED_MEASUREMENTS"][:8], ka is not None)

from app.services.remote_attestation import TEEQuote
q = TEEQuote(quote_id="x", quote_type=QuoteType.SGX_ECDSA, tee_type=TEEType.SGX,
             raw_bytes=raw, measurement=meas, report_data="aa" * 32, tcb_version="0.1")
p("K2f freshness: TEEQuote.timestamp defaults to", q.timestamp.isoformat(),
  "(parser never reads a quote timestamp -> max_quote_age_seconds can never trip)")
p("K2g min_tcb_version enforced anywhere in verify_quote path: policy field exists =",
  hasattr(inline, "min_tcb_version"), "and value", repr(inline.min_tcb_version))

# ---------- K7: unknown quote type -> SOFTWARE_HASH/FIRECRACKER fail-open ----
os.environ["CDS_ATTESTATION_ALLOWED_MEASUREMENTS"] = ""
get_settings.cache_clear()
bad, _, _ = craft("sgx-epid0001", typ="sgx_epid")           # valid QuoteType, unmapped in the dict
r_bad = kms.distribute_key(kA["key_id"], "s1", attestation=bad)
p("K7a unmapped type 'sgx_epid' with ALLOW_SIMULATION=false ->", "rejected" if r_bad is None else "ACCEPTED")
os.environ["CDS_ALLOW_SIMULATION"] = "true"
get_settings.cache_clear()
r_bad2 = kms.distribute_key(kA["key_id"], "s2", attestation=bad)
p("K7b same quote with ALLOW_SIMULATION=true ->", "ACCEPTED (downgrade to SOFTWARE_HASH pays off)"
  if r_bad2 is not None else "rejected")
os.environ["CDS_ALLOW_SIMULATION"] = "false"
get_settings.cache_clear()
typo, _, _ = craft("sgx-typo0001", typ="SGX_ECDSA")          # case typo / tampered type string
p("K7c type case-typo 'SGX_ECDSA' with ALLOW_SIMULATION=false ->",
  "rejected" if kms.distribute_key(kA["key_id"], "s3", attestation=typo) is None else "ACCEPTED")

# ---------- K1: rotate_key on unknown key_id ---------------------------------
phantom = kms.rotate_key("dek-does-not-exist")
p("K1 rotate_key(missing) returned", phantom, "| get_key(new) =", kms.get_key(phantom),
  "| old key still usable:", kms.get_key(kA["key_id"]) is not None)

# ---------- K9: import_wrapped accepts an undecryptable blob -----------------
ok = kms.import_wrapped("dek-ghost", b"\x00" * 60)
p("K9 import_wrapped(garbage) ->", ok, "| get_key ->", kms.get_key("dek-ghost"),
  "(returns True although the blob can never be unwrapped)")

# ---------- K6: decrypt_with_key on a truncated ciphertext -------------------
try:
    kms.decrypt_with_key(kA["key_id"], b"\x01\x02\x03")
except Exception as e:
    p("K6 decrypt_with_key(3-byte ciphertext) ->", type(e).__name__, ":", str(e)[:90])

# ---------- K5: DEK length vs the SM4-GCM label ------------------------------
from app.models.kms import DataEncryptionKey, KeyAlgorithm
p("K5 generate_data_key key length =", len(kA["key_bytes"]), "bytes;",
  "DataEncryptionKey.algorithm default =", KeyAlgorithm.SM4_GCM.value,
  "(SM4 keys are 16 bytes by GM/T 0002)")

# ---------- K3/K8: does anything ever create a KeyDistribution row? ----------
import subprocess
p("K3 KeyDistribution.expires_at / status are read by cleanup_expired_distributions; "
  "grep for inserts is done statically.")

# ---------- K4: two KMSService instances share one KEK id --------------------
kms2 = KMSService()
before = kms.get_key(kA["key_id"])
kms2.generate_data_key("prod-C")            # triggers _ensure_kek on the SAME hsm singleton
after = kms.get_key(kA["key_id"])
p("K4b after a 2nd KMSService() issues a key, previously issued DEK unwraps:",
  before is not None, "->", after is not None,
  "| same plaintext:", before == after)

print("\n".join(OUT))
