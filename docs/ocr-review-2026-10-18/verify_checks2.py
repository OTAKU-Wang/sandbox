# -*- coding: utf-8 -*-
"""Executed checks #2 — column_encryption.py / deterministic_sm4.py + leftover cert checks."""
import os, sys, re, types
sys.path.insert(0, r"D:/workspace/sandbox")
os.chdir(r"C:/Users/清彧/AppData/Local/Temp/ocr-cds")
os.environ.setdefault("CDS_DATABASE_URL", "sqlite+aiosqlite:///./_verify_tmp.db")
os.environ.setdefault("CDS_DEBUG", "true")

def H(t): print("\n" + "=" * 78 + "\n### " + t + "\n" + "=" * 78)
def T(label, fn):
    try:
        print(f"[{label}] -> {fn()!r}")
    except Exception as e:
        print(f"[{label}] RAISED {type(e).__name__}: {str(e)[:150]}")

H("A. module import reality: which backend is armed?")
from app.services import column_encryption as ce
from app.services import deterministic_sm4 as d4
print("  column_encryption._HAS_GMSSL   =", ce._HAS_GMSSL)
print("  deterministic_sm4._HAS_GMSSL   =", d4._HAS_GMSSL)
print("  column_encryption module file  =", ce.__file__)
print("  singleton key_id               =", ce.column_encryption.key_id, "(default DEK active:",
      ce.column_encryption.dek == ce._default_dek, ")")

H("B. finding[25]/[30] _pkcs7_unpad on empty / short input")
T("ce._pkcs7_unpad(b'')", lambda: ce.ColumnEncryption._pkcs7_unpad(b""))
T("d4._pkcs7_unpad(b'')", lambda: d4._pkcs7_unpad(b""))
T("d4._pkcs7_unpad(b'\\x01')", lambda: d4._pkcs7_unpad(b"\x01"))
T("d4._pkcs7_pad(b'x', block_size=8) then unpad (hardcoded 16)",
  lambda: d4._pkcs7_unpad(d4._pkcs7_pad(b"x", 8)))

H("C. finding[11] decrypt_deterministic length validation")
E = ce.EncryptedColumn; M = ce.EncryptionMode
c = ce.ColumnEncryption(dek=b"0123456789abcdef", key_id="k")
enc = c.encrypt_deterministic("hello-world", "phone")
print("  legit ciphertext len:", len(enc.ciphertext), "(IV16 + ct)")
for label, ct in (("len==16 (IV only, empty ct)", b"\x00" * 16),
                  ("len==20 (IV + 4 bytes, not block aligned)", b"\x00" * 20),
                  ("len==15 (too short)", b"\x00" * 15)):
    T(f"gmssl backend decrypt {label}",
      lambda ct=ct: c.decrypt_deterministic(E(ciphertext=ct, mode=M.DETERMINISTIC, column_name="phone", key_id="k")))
ce._HAS_GMSSL = False
for label, ct in (("len==16 (AES path)", b"\x00" * 16), ("len==20 (AES path)", b"\x00" * 20)):
    T(f"AES fallback decrypt {label}",
      lambda ct=ct: c.decrypt_deterministic(E(ciphertext=ct, mode=M.DETERMINISTIC, column_name="phone", key_id="k")))
ce._HAS_GMSSL = True
print("  gmssl pkcs7_unpadding source:", __import__("inspect").getsource(
    __import__("gmssl.func", fromlist=["pkcs7_unpadding"]).pkcs7_unpadding).strip())

H("D. finding[10]/[14]/[31] cross-backend ciphertext + index compatibility")
c2 = ce.ColumnEncryption(dek=b"0123456789abcdef", key_id="k")
sm4_ct = c2.encrypt_deterministic("91|张三|13800000000", "phone").ciphertext
sm4_hash = c2.hash_for_index("13800000000", "phone")
iv_sm4 = c2._derive_iv_siv(c2.dek, "phone", b"13800000000")
ce._HAS_GMSSL = False
aes_ct = c2.encrypt_deterministic("91|张三|13800000000", "phone").ciphertext
aes_hash = c2.hash_for_index("13800000000", "phone")
iv_aes = c2._derive_iv_siv(c2.dek, "phone", b"13800000000")
ce._HAS_GMSSL = True
print("  gmssl ct :", sm4_ct.hex()[:48], f"({len(sm4_ct)}B)")
print("  aes   ct :", aes_ct.hex()[:48], f"({len(aes_ct)}B)  equal={sm4_ct == aes_ct}")
print("  gmssl idx:", sm4_hash[:32], "... len", len(sm4_hash))
print("  aes   idx:", aes_hash[:32], "... len", len(aes_hash), " equal=", sm4_hash == aes_hash)
print("  gmssl IV :", iv_sm4.hex(), "  aes IV:", iv_aes.hex(), " equal=", iv_sm4 == iv_aes)
def _cross_read():
    ce._HAS_GMSSL = False
    return c2.decrypt_deterministic(E(ciphertext=sm4_ct, mode=M.DETERMINISTIC,
                                      column_name="phone", key_id="k"))
T("read gmssl-written ciphertext with AES backend", _cross_read)
ce._HAS_GMSSL = True
T("read AES-written ciphertext with gmssl backend", lambda:
   c2.decrypt_deterministic(E(ciphertext=aes_ct, mode=M.DETERMINISTIC, column_name="phone", key_id="k")))
d = d4.DeterministicSM4(b"0123456789abcdef")
d_sm4 = d.encrypt("13800000000"); d_h = d.hash_for_index("13800000000")
d4._HAS_GMSSL = False
d_aes = d.encrypt("13800000000"); d_h2 = d.hash_for_index("13800000000")
print("  deterministic_sm4: gmssl ct", d_sm4.hex()[:32], "aes ct", d_aes.hex()[:32], "equal=", d_sm4 == d_aes)
print("  deterministic_sm4: gmssl hash", d_h, "aes hash", d_h2, "equal=", d_h == d_h2)
T("deterministic_sm4 decrypt gmssl-ct under AES backend", lambda: (
    setattr(d4, "_HAS_GMSSL", False), d.decrypt(d_sm4))[1])
d4._HAS_GMSSL = True
print("  IV derivation in deterministic_sm4 also flips: gmssl", d._iv.hex(), "aes",
      end=" ")
d4._HAS_GMSSL = False; print(d._derive_iv(b"0123456789abcdef").hex())
d4._HAS_GMSSL = True

H("E. finding[12] AEAD with adata=None: cross-column / cross-row substitution")
src_ct = c2.encrypt_randomized("amount=999999", "tax_amount")
forged = E(ciphertext=src_ct.ciphertext, mode=M.RANDOMIZED, column_name="loan_amount", key_id="k")
print("  ciphertext minted for column 'tax_amount' replayed onto 'loan_amount' decrypts to:",
      repr(c2.decrypt_randomized(forged)))
print("  adata actually passed to AESGCM.encrypt:",
      re.search(r"aesgcm\.encrypt\(nonce, data, (None|\w+)\)",
                open(r"D:/workspace/sandbox/app/services/column_encryption.py", encoding="utf-8").read()).group(1))

H("F. finding[13] decrypt_row swallows failures and returns ciphertext as plaintext")
row = {"phone": "13800000000", "email": "a@b.com", "note": "1234", "code": "deadbeef" * 6}
encr = c2.encrypt_row(row, ["phone", "email", "note", "code"])
print("  encrypted row:", {k: (v[:24] + "...") if isinstance(v, str) and len(v) > 24 else v
                          for k, v in encr.items()})
ok = c2.decrypt_row(encr, ["phone", "email", "note", "code"], M.DETERMINISTIC)
print("  decrypt_row(correct mode)      ->", ok)
bad = c2.decrypt_row(encr, ["phone", "email", "note", "code"], M.RANDOMIZED)
print("  decrypt_row(WRONG mode)        ->", bad)
print("  == ciphertext echoed back as plaintext:",
      bad["phone"] == encr["phone"], "| no exception, no log -> silent")
c2b = ce.ColumnEncryption(dek=b"ffffffffffffffff", key_id="other")
print("  decrypt_row with the WRONG DEK   ->",
      {k: (v[:20] + "...") for k, v in c2b.decrypt_row(encr, ["phone", "email"], M.DETERMINISTIC).items()})

H("G. finding[28]/[32] no domain separation in keyed hashing")
print("  _derive_iv_siv(dek,'ab','cX') == _derive_iv_siv(dek,'a','bcX') :",
      ce.ColumnEncryption._derive_iv_siv(c2.dek, "ab", b"cX") ==
      ce.ColumnEncryption._derive_iv_siv(c2.dek, "a", b"bcX"))
print("  hash_for_index(dek,'ab','cX') == hash_for_index(dek,'a','bcX') :",
      c2.hash_for_index("cX", "ab") == c2.hash_for_index("bcX", "a"))
a = c2.encrypt_deterministic("cX", "ab").ciphertext
b = c2.encrypt_deterministic("bcX", "a").ciphertext
print("  -> identical ciphertext across two DIFFERENT columns:", a == b, a.hex()[:32])
d1 = d4.DeterministicSM4(b"0123456789abcdef")
print("  deterministic_sm4.hash_for_index signature has no table/column:",
      "def hash_for_index(self, plaintext" in
      open(r"D:/workspace/sandbox/app/services/deterministic_sm4.py", encoding="utf-8").read())
print("  same plaintext, two DeterministicSM4 uses (phone vs ent_id) -> same index hash:",
      d1.hash_for_index("91330000") == d1.hash_for_index("91330000"))
print("  IV/hash is the keyed digest, and the IV is transmitted in the ciphertext header:",
      c2.encrypt_deterministic("13800000000", "phone").ciphertext[:16].hex(),
      "== first16 of SM3(dek||column||plaintext):",
      c2._derive_iv_siv(c2.dek, "phone", b"13800000000").hex())

H("H. finding[17]/[26]/[27]/[33] error classification in the AEAD decrypt paths")
r = d4.RandomizedSM4(b"0123456789abcdef")
enc_rand = r.encrypt("secret")
print("  legit randomized ct len:", len(enc_rand), "(nonce12+ct+tag16)")
T("deterministic_sm4 RandomizedSM4.decrypt(truncated 20B)", lambda: r.decrypt(enc_rand[:20]))
T("deterministic_sm4 RandomizedSM4.decrypt(len 12)", lambda: r.decrypt(b"\x00" * 12))
T("deterministic_sm4 RandomizedSM4.decrypt(len 27) passes the <12 guard?", lambda: r.decrypt(b"\x00" * 27))
T("ce.decrypt_randomized(16B blob)", lambda: c2.decrypt_randomized(
    E(ciphertext=b"\x00" * 16, mode=M.RANDOMIZED, column_name="x", key_id="k")))
T("ce.decrypt_gcm(wrong arg order -> non-utf8 plaintext)", lambda: c2.decrypt_gcm(*reversed(c2.encrypt_gcm("x"))))
ct, nonce, tag = c2.encrypt_gcm("payload")
T("ce.decrypt_gcm(tag truncated)", lambda: c2.decrypt_gcm(ct, nonce, tag[:8]))
src_txt = open(r"D:/workspace/sandbox/app/services/column_encryption.py", encoding="utf-8").read()
print("  'raise ... from' used anywhere in the module:", " from err" in src_txt, "| from e:", " from e" in src_txt)

H("I. finding[34] register() accepts nonsense")
reg = d4.ColumnEncryptionRegistry()
T("register(level='Det') typo", lambda: reg.register("t", "c", "Det") or "no error")
T("register(level='det') without enc_column", lambda: reg.register("t", "c2", "det") or "no error")
T("re-register silently overwrites", lambda: (
    reg.register("t", "c3", "det", enc_column="e1"), reg.register("t", "c3", "plain"),
    reg.get("t", "c3"))[2])
print("  registry is the query rewriter's only 'what must be encrypted' source:",
      "from app.services.deterministic_sm4 import" in
      open(r"D:/workspace/sandbox/app/services/query_rewriter.py", encoding="utf-8").read())

H("J. finding[36] unused import struct")
print("  occurrences of 'struct' in column_encryption.py:", re.findall(r"\bstruct\b", src_txt))

H("K. finding[29] default DEK gating")
from app.core.config import get_settings, is_production
import inspect
print("  is_production source:")
print("  " + inspect.getsource(is_production).strip().replace("\n", "\n  "))
s = get_settings()
print("  is_production(default test-ish settings) =", is_production(s))
s2 = s.model_copy(update={"DEBUG": True}) if hasattr(s, "model_copy") else None
try:
    print("  is_production(DEBUG=True) =", is_production(s2))
except Exception as e:
    print("  is_production(DEBUG=True) check failed:", e)
print("  hardcoded DEK const present:", b"cds-column-encryption-default-key" in
      open(r"D:/workspace/sandbox/app/services/column_encryption.py", "rb").read())
print("  grep validate_security_config for COLUMN_ENCRYPTION_KEY:")
cfg = open(r"D:/workspace/sandbox/app/core/config.py", encoding="utf-8").read()
for i, ln in enumerate(cfg.splitlines(), 1):
    if "COLUMN_ENCRYPTION_KEY" in ln:
        print(f"    config.py:{i}: {ln.strip()[:140]}")

H("L. leftover cert checks: UTCTime 2050 + model types + sqlite naive compare")
from app.services import certificate_service as cs
print("  CA_VALIDITY_DAYS occurrences in the module:",
      len(re.findall(r"CA_VALIDITY_DAYS",
                     open(r"D:/workspace/sandbox/app/services/certificate_service.py", encoding="utf-8").read())))
u = cs._encode_utctime(__import__("datetime").datetime(2050, 1, 1))
print("  _encode_utctime(2050-01-01) =", u.hex(), u[2:].decode(), "-> _decode_time gives",
      cs.CertificateService._decode_time(u[2:]))
u2 = cs._encode_utctime(__import__("datetime").datetime(2126, 5, 5))
print("  _encode_utctime(2126-05-05) =", u2[2:].decode(), "-> decodes to",
      cs.CertificateService._decode_time(u2[2:]))
from app.models.certificate import Certificate as CM
print("  Certificate.not_after type:", CM.__table__.columns["not_after"].type,
      "| not_before:", CM.__table__.columns["not_before"].type)
print("  serial_number unique:", CM.__table__.columns["serial_number"].unique)
import sqlite3
cx = sqlite3.connect(":from memory:" if False else ":memory:")
cx.execute("create table t(d datetime)")
cx.execute("insert into t values (?)", (__import__("datetime").datetime(2026, 1, 1),))
got = cx.execute("select d from t").fetchone()[0]
print("  raw sqlite datetime round-trip:", type(got).__name__, repr(got), "tzinfo:", got.tzinfo)
try:
    _ = __import__("datetime").datetime.now(__import__("datetime").timezone.utc) < got
    print("  aware_now < naive_db_value: no error")
except TypeError as e:
    print("  aware_now < naive_db_value ->", type(e).__name__ + ":", e)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
print("  SQLAlchemy sqlite DateTime(timezone=True) round-trip:")
eng = create_engine("sqlite://")
from sqlalchemy import MetaData, Table, Column
md = MetaData(); tb = Table("t", md, Column("d", CM.__table__.columns["not_after"].type))
md.create_all(eng)
with Session(eng) as s3:
    s3.execute(tb.insert().values(d=__import__("datetime").datetime(2026, 1, 1, tzinfo=__import__("datetime").timezone.utc)))
with Session(eng) as s3:
    v = s3.execute(tb.select()).scalar_one()
print("    read back:", type(v).__name__, repr(v), "tzinfo:", v.tzinfo)
