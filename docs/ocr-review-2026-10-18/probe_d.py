
import sys, os
sys.path.insert(0, r"D:/workspace/sandbox")
os.environ.setdefault("TESTING", "1")
from app.services.mutual_tls import SM2CertificateAuthority, CertificateInfo
ca = SM2CertificateAuthority(); ca_cert = ca.generate_ca_certificate()
kp = ca._crypto.generate_keypair()
P = ca.issue_certificate("victim-space", kp.public_key)
def rebuild(c):
    d = {"subject": c.subject, "issuer": c.issuer, "serial": c.serial_number,
         "not_before": c.not_before.isoformat(), "not_after": c.not_after.isoformat(),
         "public_key": c.public_key, "is_ca": c.is_ca}
    if c.space_id: d["space_id"] = c.space_id
    return "|".join(f"{k}={v}" for k, v in sorted(d.items()))
Q = CertificateInfo(**{**P.__dict__,
                       "serial_number": P.serial_number + "|space_id=victim-space",
                       "space_id": ""})
a, b = rebuild(P), rebuild(Q)
print("payload equal:", a == b)
print("verify(P) ->", ca.verify_certificate(P, ca_cert))
print("verify(Q) ->", ca.verify_certificate(Q, ca_cert), " Q.space_id=%r Q.serial=%r" % (Q.space_id, Q.serial_number))
