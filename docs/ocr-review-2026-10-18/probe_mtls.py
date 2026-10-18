import sys, types, hashlib
mod = types.ModuleType('app.services.crypto_service')
class KP:
    def __init__(self, tag): self.private_key = 'priv-'+tag; self.public_key = 'pub-'+tag
class Sig:
    def __init__(self, s): self.signature = s
_n=[0]
class Fake:
    def generate_keypair(self):
        _n[0]+=1; return KP('k%d'%_n[0])
    def sign(self, data, private_key, public_key):
        return Sig(hashlib.sha256(private_key.encode()+b'|'+data).hexdigest())
    def verify(self, data, signature, public_key):
        if len(signature) != 64: raise ValueError('invalid hex signature')
        return hashlib.sha256((public_key.replace('pub-','priv-')).encode()+b'|'+data).hexdigest() == signature
mod.crypto_service = Fake()
sys.modules['app.services.crypto_service'] = mod
sys.path.insert(0, 'D:/workspace/sandbox')
from app.services.mutual_tls import SM2CertificateAuthority, MTLSManager, CertificateInfo
ca = SM2CertificateAuthority()
kp = ca._crypto.generate_keypair()
cac = ca.generate_ca_certificate()
c_empty = ca.issue_certificate("", kp.public_key)
print("C10 space_id='' -> verify:", ca.verify_certificate(c_empty, cac))
c_ok = ca.issue_certificate("space-1", kp.public_key)
print("C10 space_id='space-1' -> verify:", ca.verify_certificate(c_ok, cac))
mgr = MTLSManager()
a = mgr.create_identity("space-A"); b = mgr.create_identity("space-B")
print("C4 peer verify A:", mgr.verify_peer_certificate(a.certificate), "B:", mgr.verify_peer_certificate(b.certificate))
forged = CertificateInfo(subject="CDS Space evil", issuer="Totally Different CA", serial_number=c_ok.serial_number,
    not_before=c_ok.not_before, not_after=c_ok.not_after, public_key=c_ok.public_key, signature=c_ok.signature,
    is_ca=False, space_id=c_ok.space_id)
print("C3 issuer-mismatch accepted:", ca.verify_certificate(forged, cac))
leaf = ca.issue_certificate("space-L", kp.public_key)
print("C3 leaf-as-CA accepted:", ca.verify_certificate(c_ok, leaf))
print("C3 ca_cert=None ->", ca.verify_certificate(c_ok, None))
bad = CertificateInfo(subject=c_ok.subject, issuer=c_ok.issuer, serial_number=c_ok.serial_number,
    not_before=c_ok.not_before, not_after=c_ok.not_after, public_key=c_ok.public_key, signature="not-hex",
    is_ca=False, space_id=c_ok.space_id)
try: print("C10 bad signature ->", ca.verify_certificate(bad, cac))
except Exception as e: print("C10 bad signature RAISED", type(e).__name__, e)
# C3/C2: separator-injection equivalence
inj = ca.issue_certificate("x|is_ca=True", kp.public_key, subject="CDS Space y")
print("C2 crafted subject signed+verified:", ca.verify_certificate(inj, cac))
# C11 issuer hardcoded vs custom CA subject
ca2 = SM2CertificateAuthority()
mycac = ca2.generate_ca_certificate(subject="ACME Federation CA")
c2 = ca2.issue_certificate("s2", kp.public_key)
print("C11 ca.subject=%r  cert.issuer=%r" % (mycac.subject, c2.issuer))
ctx = mgr.get_tls_context("space-A")
print("C1 key_der[:16]=", repr(ctx['key_der'][:16]), "| pem line2=", repr(ctx['cert_pem'].split(chr(10))[1]))
print("C1 create_identity overwrite:", mgr.create_identity("space-A").identity_id != a.identity_id)

# --- C2 precise payload-collision demo (space_id injection) ---
import json as _j
def build(cert):
    d = {"subject": cert.subject, "issuer": cert.issuer, "serial": cert.serial_number,
         "not_before": cert.not_before.isoformat(), "not_after": cert.not_after.isoformat(),
         "public_key": cert.public_key, "is_ca": cert.is_ca}
    if cert.space_id: d["space_id"] = cert.space_id
    return "|".join(f"{k}={v}" for k, v in sorted(d.items()))
A = ca.issue_certificate("x", kp.public_key, subject="y")            # space_id=x, subject=y
B = ca.issue_certificate("x|subject=y", kp.public_key, subject="")   # injected
print("C2 collision:", build(A) == build(B))
print("C2 A payload tail:", build(A)[-40:])
print("C2 B payload tail:", build(B)[-40:])
# --- C3 issuer-not-bound-to-CA, isolated (re-signed with the CA key) ---
from datetime import datetime, timezone, timedelta
c3 = ca.issue_certificate("space-3", kp.public_key, subject="CDS Space space-3")
c3.issuer = "Attacker Picks This"
c3.signature = ca._crypto.sign(build(c3).encode(), ca._ca_keypair.private_key, ca._ca_keypair.public_key).signature
print("C3 CA-signed cert with arbitrary issuer accepted:", ca.verify_certificate(c3, cac))
c4 = ca.issue_certificate("space-4", kp.public_key); c4.is_ca = True
c4.signature = ca._crypto.sign(build(c4).encode(), ca._ca_keypair.private_key, ca._ca_keypair.public_key).signature
print("C3 leaf flipped to is_ca=True accepted:", ca.verify_certificate(c4, cac))
# --- C8-style naive datetime ---
c5 = ca.issue_certificate("space-5", kp.public_key)
c5.not_before = c5.not_before.replace(tzinfo=None)
try: print("C10 naive not_before ->", ca.verify_certificate(c5, cac))
except Exception as e: print("C10 naive not_before RAISED", type(e).__name__)
