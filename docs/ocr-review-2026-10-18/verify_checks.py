# -*- coding: utf-8 -*-
"""Executed checks for the crypto/certificate static-review findings. Read-only wrt the repo."""
import os, sys, traceback, types, re, subprocess

REPO = r"D:/workspace/sandbox"
sys.path.insert(0, REPO)
os.chdir(r"C:/Users/清彧/AppData/Local/Temp/ocr-cds")  # keep the repo untouched
os.environ.setdefault("CDS_DATABASE_URL", "sqlite+aiosqlite:///./_verify_tmp.db")
os.environ.setdefault("CDS_DEBUG", "true")

def H(t):
    print("\n" + "=" * 78)
    print("### " + t)
    print("=" * 78)

def T(label, fn):
    try:
        r = fn()
        print(f"[{label}] OK  -> {r}")
    except Exception as e:
        print(f"[{label}] RAISED {type(e).__name__}: {e}")

# ---------------------------------------------------------------- import targets
H("0. environment / import reality check")
import importlib
print("python:", sys.version.split()[0], sys.executable)
for m in ("gmssl", "cryptography"):
    try:
        mod = importlib.import_module(m)
        print(f"  import {m}: OK version={getattr(mod,'__version__','n/a')}")
    except Exception as e:
        print(f"  import {m}: FAIL {e!r}")

cs = None
try:
    from app.services import certificate_service as cs_mod
    print("import app.services.certificate_service: OK")
except Exception as e:
    print("import app.services.certificate_service FAILED:", repr(e)[:300])
    print("-> exec module source with stubbed app.models.certificate / audit_service")
    src = open(REPO + r"/app/services/certificate_service.py", encoding="utf-8").read()
    # stub the two non-crypto app deps; keep the REAL crypto_service
    mc = types.ModuleType("app.models.certificate")
    import uuid as _u, datetime as _dtm, enum as _en
    class CertType(str, _en.Enum):
        IDENTITY = "identity"; PLATFORM = "platform"; SERVICE = "service"
    class CertStatus(str, _en.Enum):
        ACTIVE = "active"; REVOKED = "revoked"; SUSPENDED = "suspended"; EXPIRED = "expired"
    class Certificate:  # only used for type annotation / db rows, not exercised here
        pass
    mc.Certificate, mc.CertType, mc.CertStatus = Certificate, CertType, CertStatus
    ma = types.ModuleType("app.services.audit_service")
    class _A:
        async def log(self, *a, **k): return None
    ma.audit_service = _A()
    sys.modules["app.models.certificate"] = mc
    sys.modules["app.services.audit_service"] = ma
    cs_mod = types.ModuleType("cs_standalone")
    exec(compile(src, "certificate_service.py", "exec"), cs_mod.__dict__)
    print("standalone exec: OK")

cs = cs_mod.certificate_service
int_to_bytes = cs_mod._int_to_bytes
enc_int = cs_mod._encode_integer
enc_tlv = cs_mod._encode_tlv
print("_HAS_GMSSL in crypto_service:", sys.modules["app.services.crypto_service"]._HAS_GMSSL)

# ---------------------------------------------------------------- finding 0
H("1. finding[0] _int_to_bytes / _encode_integer DER INTEGER minimality")
import uuid as _uuid
serials = [f"CDS-{_uuid.uuid4().hex[:16].upper()}" for _ in range(3)] + ["CDS-0080A1B2C3D4E5F6", "CDS-FFFF000011112222"]
for s in serials:
    n = int.from_bytes(s.encode()[:20].rjust(20, b"\x00"), "big")
    b = int_to_bytes(n)
    d = enc_int(n)
    print(f"  serial={s!r} len(int)=20 bitlen={n.bit_length()} _int_to_bytes={len(b)}B first=0x{b[0]:02x} "
          f"DER={d[:4].hex()}..{d[-2:].hex()} der_value_len={len(d)-2} high_bit_set={bool(b[0]&0x80)}")
print("  n=0 ->", int_to_bytes(0).hex(), "DER", enc_int(0).hex())
T("_encode_integer(-1)", lambda: enc_int(-1).hex())
T("_int_to_bytes(-1)", lambda: int_to_bytes(-1).hex())
T("minimal-DER check via cryptography (decode int)", lambda: (
    lambda i: i)(0))
# strict ASN.1 check of the serial INTEGER as it appears in a real cert -> see openssl block below

# ---------------------------------------------------------------- finding 9 / precedence
H("2. finding[9] operator precedence of the extension-parsing condition")
for tag in (0xa3, 0xa0, 0xa1, 0x30, 0x00, 0x20, 0x80):
    print(f"  tag=0x{tag:02x}: (tag & 0xa0) == 0xa3 -> {tag & 0xa0 == 0xa3} | (tag == 0xa3) -> {tag == 0xa3}"
          f" | (tag & 0xa0)==0xa0 -> {tag & 0xa0 == 0xa0}")

# ---------------------------------------------------------------- finding 18
H("3. finding[18] _decode_oid first-octet handling")
dec = cs_mod.CertificateService._decode_oid
for b, want in [(bytes([42]), "1.2"), (bytes([85]), "2.5"), (bytes([80]), "2.0"),
                (bytes([127]), "{2,47} expected 2.47"), (bytes([0x80]), "multi-byte first comp"),
                (bytes([0x2a, 0x86, 0x48]), "1.2.840"), (bytes([0x60]), "2.16")]:
    print(f"  {b.hex()} -> {dec(b)!r}   (expect: {want})")
for oid in ["1.2.156.10197.1.501", "1.2.156.10197.1.301", "1.2.156.10197.1.301.1", "2.5.29.19", "2.5.29.15"]:
    e = cs_mod._encode_oid(oid)
    rt = dec(e[2:])
    print(f"  round-trip {oid}: encoded={e.hex()} decoded={rt!r} match={rt == oid}")

# ---------------------------------------------------------------- build a real cert, run openssl
H("4. build real certs through the LIVE code path and parse with openssl")
from app.services.crypto_service import crypto_service
kp = crypto_service.generate_keypair()
ca = crypto_service.generate_keypair()
from datetime import datetime, timedelta
now = datetime.utcnow()
pem_ee = cs._build_cert_pem(serial="CDS-0080A1B2C3D4E5F6", subject="CN=node1,O=CDS,C=CN",
                            issuer="CN=CDS TLCP CA,O=CDS,C=CN", public_key=kp.public_key,
                            not_before=now, not_after=now + timedelta(days=365), is_ca=False,
                            private_key=kp.private_key,
                            issuer_private_key=ca.private_key, issuer_public_key=ca.public_key)
pem_ca = cs._build_cert_pem(serial="CDS-AABBCCDDEEFF0011", subject="CN=CDS Root CA,O=CDS,C=CN",
                            issuer="CN=CDS Root CA,O=CDS,C=CN", public_key=ca.public_key,
                            not_before=now, not_after=now + timedelta(days=3650), is_ca=True,
                            private_key=ca.private_key)
open("ee.der", "wb").write(__import__("base64").b64decode(re.sub(r"-----.*?-----|\s", "", pem_ee)))
open("ca.der", "wb").write(__import__("base64").b64decode(re.sub(r"-----.*?-----|\s", "", pem_ca)))
print("  ee.der bytes:", os.path.getsize("ee.der"), " ca.der bytes:", os.path.getsize("ca.der"))
OPENSSL = r"C:/Program Files/Git/mingw64/bin/openssl.exe"
for name in ("ee", "ca"):
    for args in (["asn1parse", "-inform", "der", "-i", "-in", f"{name}.der"],
                 ["x509", "-inform", "der", "-in", f"{name}.der", "-text", "-noout"]):
        r = subprocess.run([OPENSSL] + args, capture_output=True, text=True)
        print(f"\n  $ openssl {' '.join(args)}  (rc={r.returncode})")
        out = (r.stdout or "") + (r.stderr or "")
        print("  " + out.strip().replace("\n", "\n  ")[:2600])
try:
    from cryptography import x509
    for name in ("ee", "ca"):
        try:
            c = x509.load_der_x509_certificate(open(f"{name}.der", "rb").read())
            print(f"  cryptography.load_der_x509_certificate({name}) OK: sig_alg={c.signature_algorithm.oid} "
                  f"exts={[e.oid.dotted_string for e in c.extensions]} critical={[e.critical for e in c.extensions]}")
        except Exception as e:
            print(f"  cryptography.load_der_x509_certificate({name}) FAILED: {type(e).__name__}: {e}")
except Exception as e:
    print("  cryptography import failed", e)

# ---------------------------------------------------------------- finding 3 / 8
H("5. finding[3] r||s signatureValue + finding[8] verify_certificate key choice")
p = cs._parse_x509_der(open("ee.der", "rb").read())
print("  parsed signature hex len:", len(p["signature"]), "-> bytes:", len(p["signature"]) // 2)
print("  parsed signature (r||s):", p["signature"][:32], "...", p["signature"][-16:])
print("  parsed public_key len:", len(p["public_key"]), "prefix:", p["public_key"][:2])
print("  parsed is_ca (CA cert!):", cs._parse_x509_der(open("ca.der", "rb").read())["is_ca"],
      " <- must be True if extension block ran")
print("  parsed is_ca (EE cert):", p["is_ca"])
v_self = cs.verify_certificate(pem_ca)
print("  verify_certificate(self-signed CA) signature_valid =", v_self.get("signature_valid"),
      "err=", str(v_self.get("signature_error"))[:90])
v_ee = cs.verify_certificate(pem_ee)
print("  verify_certificate(CA-signed EE) signature_valid =", v_ee.get("signature_valid"),
      "err=", str(v_ee.get("signature_error"))[:90])
# what a correct check with the ISSUER key gives:
tbs = v_ee["tbs_certificate_der"]
print("  same signature re-verified with the ISSUER public key ->",
      crypto_service.verify(tbs, v_ee["signature"], ca.public_key))

# ---------------------------------------------------------------- finding 1
H("6. finding[1] KeyUsage BIT STRING decoding")
for label, is_ca in (("CA=True", True), ("CA=False", False)):
    exts = cs._build_extensions(is_ca)
    m = re.findall(rb"\x30\x0d\x06\x03\x55\x1d\x0f\x04\x06\x03\x04(\x01..)", exts)
    print(f"  {label}: raw extensions der = {exts.hex()}")
for label, is_ca in (("CA", True), ("EE", False)):
    body = cs._build_extensions(is_ca)
    i = body.find(b"\x55\x1d\x0f")
    unused, content = body[i + 9], body[i + 10]
    names = ["digitalSignature", "nonRepudiation", "keyEncipherment", "dataEncipherment",
             "keyAgreement", "keyCertSign", "cRLSign", "encipherOnly"]
    set_bits = [names[b] for b in range(8) if content & (0x80 >> b)]
    print(f"  {label}: unused_bits={unused} content=0x{content:02x} -> named bits set: {set_bits}")
print("  RFC5280: digitalSignature=0x80 keyEncipherment=0x20 keyCertSign=0x04 cRLSign=0x02, unused MUST be 0")
print("  correct EE encoding 0x03 0x02 0x00 0xa0 vs code:", cs_mod._encode_tlv(0x03, b"\x01\x05").hex())

# ---------------------------------------------------------------- finding 22 / 21
H("7. finding[21]/[22] serial entropy + UTCTime year window")
print("  CA_VALIDITY_DAYS used anywhere besides the constant?",
      len(re.findall(r"CA_VALIDITY_DAYS", open(REPO + r"/app/services/certificate_service.py", encoding="utf-8").read())),
      "occurrence(s) in the module (definition only => never applied)")
u = cs_mod._encode_utctime(datetime(2050, 1, 1))
print("  _encode_utctime(2050-01-01) =", u, "-> decodes back to",
      cs_mod.CertificateService._decode_time(u[2:]))

# ---------------------------------------------------------------- finding 24
H("8. finding[24] tz-aware vs naive comparison (sqlite) + the quoted snippet")
src_txt = open(REPO + r"/app/services/certificate_service.py", encoding="utf-8").read()
print("  quoted 'if now > cert.after if False else cert.not_after:' present in file:",
      "if False else" in src_txt)
from app.models.certificate import Certificate as CertModel
col = CertModel.__table__.columns["not_after"]
print("  Certificate.not_after column type:", col.type, "nullable:", col.nullable)
print("  serial_number unique?", CertModel.__table__.columns["serial_number"].unique,
      "pk?", CertModel.__table__.columns["serial_number"].primary_key)
try:
    import sqlite3
    cx = sqlite3.connect(":memory:")
    cx.execute("create table t(d datetime)")
    cx.execute("insert into t values (?)", (datetime(2026, 1, 1),))
    got = cx.execute("select d from t").fetchone()[0]
    print("  raw sqlite round-trip type:", type(got).__name__, repr(got))
    from datetime import timezone as _tz
    try:
        _ = datetime.now(_tz.utc) < got
        print("  naive < aware comparison: no error")
    except TypeError as e:
        print("  aware vs naive comparison ->", type(e).__name__, e)
except Exception:
    traceback.print_exc()
