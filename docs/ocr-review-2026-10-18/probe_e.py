
import sys, os
sys.path.insert(0, r"D:/workspace/sandbox")
os.environ.setdefault("TESTING", "1")
from app.services.mutual_tls import SM2CertificateAuthority, CertificateInfo
ca = SM2CertificateAuthority()
kp = ca._crypto.generate_keypair()
P = ca.issue_certificate("sp-x", kp.public_key)
print("verify(cert, ca_cert=None)  ->", ca.verify_certificate(P))          # C3 fallback
print("verify(cert, ca_cert=leaf)  ->", ca.verify_certificate(P, P))       # C3 leaf as CA (no is_ca check)
P2 = CertificateInfo(**{**P.__dict__, "issuer": "Totally Unrelated CA"})
print("issuer forged, re-signed?   ->", ca.verify_certificate(P2, ca.generate_ca_certificate()))
other = SM2CertificateAuthority()
print("verify with a DIFFERENT ca  ->", other.verify_certificate(P, other.generate_ca_certificate()))
mgr_ca = ca.generate_ca_certificate(subject="Custom CA")
print("ca.is_ca flag never read in verify: issuer-mismatch accepted ->",
      ca.verify_certificate(P, mgr_ca))
