
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
Q = CertificateInfo(**{**P.__dict__, "public_key": P.public_key + "|space_id=victim-space", "space_id": ""})
a, b = rebuild(P), rebuild(Q)
print("len", len(a), len(b), "eq", a == b)
i = next((i for i in range(min(len(a), len(b))) if a[i] != b[i]), min(len(a), len(b)))
print("first diff @", i)
print("P:", a[max(0,i-60):i+60])
print("Q:", b[max(0,i-60):i+60])
