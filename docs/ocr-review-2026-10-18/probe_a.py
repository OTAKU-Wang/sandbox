
import sys, os
sys.path.insert(0, r"D:/workspace/sandbox")
os.environ.setdefault("TESTING", "1")
os.environ.setdefault("CDS_DATABASE_URL", "sqlite+aiosqlite:///./ci.db")

def hdr(t): print("\n" + "="*18, t)

hdr("C2 payload collision")
def payload(d): return "|".join(f"{k}={v}" for k, v in sorted(d.items()))
a = {"subject": "CDS Space x|is_ca=True", "issuer": "CDS Federation CA", "serial": "s1",
     "not_before": "t1", "not_after": "t2", "public_key": "pk", "space_id": "x", "is_ca": False}
b = {"subject": "CDS Space x", "issuer": "CDS Federation CA", "serial": "s1",
     "not_before": "t1", "not_after": "t2", "public_key": "pk", "is_ca": True}
print("collision:", payload(a) == payload(b))

hdr("C6 empty space_id round-trip")
from app.services.mutual_tls import SM2CertificateAuthority, CertificateInfo
from datetime import datetime, timezone, timedelta
ca = SM2CertificateAuthority()
print("available:", ca.available)
if ca.available:
    ca_cert = ca.generate_ca_certificate()
    kp = ca._crypto.generate_keypair()
    cert_empty = ca.issue_certificate("", kp.public_key)
    print("issued space_id=%r subject=%r" % (cert_empty.space_id, cert_empty.subject))
    print("verify(empty space_id) ->", ca.verify_certificate(cert_empty, ca_cert))
    cert_ok = ca.issue_certificate("space-real", kp.public_key)
    print("verify(non-empty)      ->", ca.verify_certificate(cert_ok, ca_cert))
    naive = CertificateInfo(subject=cert_ok.subject, issuer=cert_ok.issuer, serial_number=cert_ok.serial_number,
        not_before=datetime.now(), not_after=datetime.now()+timedelta(days=1),
        public_key=cert_ok.public_key, signature=cert_ok.signature, space_id="space-real")
    try:
        print("naive datetime ->", ca.verify_certificate(naive, ca_cert))
    except Exception as e:
        print("naive datetime ->", type(e).__name__, str(e)[:120])
    bad = CertificateInfo(subject=cert_ok.subject, issuer=cert_ok.issuer, serial_number=cert_ok.serial_number,
        not_before=cert_ok.not_before, not_after=cert_ok.not_after,
        public_key=cert_ok.public_key, signature="not-hex", space_id="space-real")
    try:
        print("bad signature ->", ca.verify_certificate(bad, ca_cert))
    except Exception as e:
        print("bad signature ->", type(e).__name__, str(e)[:120])

hdr("C7 issuer vs CA subject")
if ca.available:
    ca2 = SM2CertificateAuthority()
    myca = ca2.generate_ca_certificate(subject="Regional Health Space CA")
    kp2 = ca2._crypto.generate_keypair()
    leaf = ca2.issue_certificate("sp-1", kp2.public_key)
    print("ca.subject =", myca.subject, "| leaf.issuer =", leaf.issuer, "| equal:", leaf.issuer == myca.subject)
