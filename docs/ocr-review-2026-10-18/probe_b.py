
import sys, os, ssl, pathlib
sys.path.insert(0, r"D:/workspace/sandbox")
os.environ.setdefault("TESTING", "1")
os.environ.setdefault("CDS_DATABASE_URL", "sqlite+aiosqlite:///./ci.db")
def hdr(t): print("\n" + "="*18, t)

TMP = pathlib.Path(r"C:/Users/清彧/AppData/Local/Temp/ocr-cds/mat")
TMP.mkdir(parents=True, exist_ok=True)

# ---------- C2: real collision between two different field sets ----------
hdr("C2 collision via public_key injection + empty space_id omission")
from app.services.mutual_tls import SM2CertificateAuthority, CertificateInfo
from datetime import timedelta
ca = SM2CertificateAuthority(); ca_cert = ca.generate_ca_certificate()
kp = ca._crypto.generate_keypair()
P = ca.issue_certificate("victim-space", kp.public_key)
def pl(d): return "|".join(f"{k}={v}" for k, v in sorted(d.items()))
def rebuild(c):
    d = {"subject": c.subject, "issuer": c.issuer, "serial": c.serial_number,
         "not_before": c.not_before.isoformat(), "not_after": c.not_after.isoformat(),
         "public_key": c.public_key, "is_ca": c.is_ca}
    if c.space_id: d["space_id"] = c.space_id
    return pl(d)
Q = CertificateInfo(**{**P.__dict__, "public_key": kp.public_key + "|space_id=victim-space", "space_id": ""})
print("payload equal:", rebuild(P) == rebuild(Q))
print("verify(P) ->", ca.verify_certificate(P, ca_cert))
print("verify(Q) ->", ca.verify_certificate(Q, ca_cert), " (Q asserts space_id=%r, pk=%r)" % (Q.space_id, Q.public_key[-22:]))

# ---------- C1: the pseudo-PEM cannot be parsed by OpenSSL ----------
hdr("C1 pseudo-PEM parse")
from app.services.mutual_tls import MTLSManager
mgr = MTLSManager()
ident = mgr.create_identity("space-c1")
ctxd = mgr.get_tls_context("space-c1")
print("cert_pem head:", repr(ctxd["cert_pem"][:70]))
print("key_der head :", repr(ctxd["key_der"][:24]), "len", len(ctxd["key_der"]))
try:
    ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cadata=ctxd["cert_pem"])
    print("OpenSSL parsed it: OK (unexpected)")
except Exception as e:
    print("OpenSSL parse ->", type(e).__name__, str(e)[:90])

# ---------- C5: cert/key pair from an unrelated CA passes validation ----------
hdr("C5 no CA<->cert trust check")
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import datetime as dt
def selfsigned(name):
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    n = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = dt.datetime.now(dt.timezone.utc)
    c = x509.CertificateBuilder().subject_name(n).issuer_name(n).public_key(k.public_key()) \
        .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1)) \
        .not_valid_after(now - dt.timedelta(days=1)).sign(k, hashes.SHA256())  # EXPIRED on purpose
    return (k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()), c.public_bytes(serialization.Encoding.PEM))
kA, cA = selfsigned("ca-A")
kB, cB = selfsigned("leaf-B")
(TMP/"ca.pem").write_bytes(cA); (TMP/"cert.pem").write_bytes(cB); (TMP/"key.pem").write_bytes(kB)
from app.core.config import get_settings
s = get_settings()
s.FEDERATION_CA_BUNDLE = str(TMP/"ca.pem"); s.FEDERATION_CERT = str(TMP/"cert.pem"); s.FEDERATION_KEY = str(TMP/"key.pem")
from app.services.mutual_tls import load_federation_mtls_material
try:
    print("unrelated CA + EXPIRED client cert ->", load_federation_mtls_material())
except Exception as e:
    print("->", type(e).__name__, str(e)[:120])

# ---------- C8: exception types escaping the ValueError contract ----------
hdr("C8 exception types from _read_pem")
(TMP/"junk.bin").write_bytes(b"\xff\xfe\x00not pem at all")
s.FEDERATION_CA_BUNDLE = str(TMP/"junk.bin")
try:
    load_federation_mtls_material()
except Exception as e:
    print("non-UTF8 file ->", type(e).__name__, "| isinstance(ValueError):", isinstance(e, ValueError))
s.FEDERATION_CA_BUNDLE = str(TMP/"ca.pem")
orig = pathlib.Path.read_text
def boom(self, *a, **k): raise PermissionError(13, "Permission denied", str(self))
pathlib.Path.read_text = boom
try:
    load_federation_mtls_material()
except Exception as e:
    print("unreadable file ->", type(e).__name__, "| isinstance(ValueError):", isinstance(e, ValueError))
finally:
    pathlib.Path.read_text = orig
