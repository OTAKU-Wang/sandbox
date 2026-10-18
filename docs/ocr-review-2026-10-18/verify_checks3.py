# -*- coding: utf-8 -*-
import os, sys, re, asyncio
sys.path.insert(0, r"D:/workspace/sandbox")
os.chdir(r"C:/Users/清彧/AppData/Local/Temp/ocr-cds")
os.environ.setdefault("CDS_DATABASE_URL", "sqlite+aiosqlite:///./_verify_tmp.db")
os.environ.setdefault("CDS_DEBUG", "true")

def H(t): print("\n" + "=" * 74 + "\n### " + t + "\n" + "=" * 74)

H("M. KeyUsage / BasicConstraints bytes, decoded (fixed offsets)")
from app.services import certificate_service as cs
from datetime import datetime, timedelta
from app.services.crypto_service import crypto_service
names = ["digitalSignature", "nonRepudiation", "keyEncipherment", "dataEncipherment",
         "keyAgreement", "keyCertSign", "cRLSign", "encipherOnly"]
for label, is_ca in (("is_ca=True (CA)", True), ("is_ca=False (EE, the live TLCP path)", False)):
    body = cs.certificate_service._build_extensions(is_ca)
    i = body.find(b"\x55\x1d\x0f")            # OID 2.5.29.15
    unused, content = body[i + 7], body[i + 8]
    bits = [names[b] for b in range(8) if content & (0x80 >> b)]
    j = body.find(b"\x55\x1d\x13")            # OID 2.5.29.19
    bc = body[j + 5:]
    print(f"  {label}")
    print(f"    KeyUsage BIT STRING = {body[i+5:i+9].hex()}  unused_bits={unused} content=0x{content:02x}"
          f" -> {bits}")
    print(f"    basicConstraints ext DER = {body[j-4:][:13].hex()}  (critical flag present: "
          f"{'no' if body[j-4+2+1] == 0x04 else '?'}) bc value = {bc.hex()}")
print("  RFC5280 correct EE KeyUsage = 03 02 00 A0 (digitalSignature|keyEncipherment, unused=0)")
print("  RFC5280 correct BC CA=TRUE  = 30 03 01 01 FF (BOOLEAN), CA=FALSE = 30 00")

H("N. finding[4] does 'except Exception' catch asyncio.CancelledError?")
print("  CancelledError MRO:", [c.__name__ for c in asyncio.CancelledError.__mro__])
try:
    try:
        raise asyncio.CancelledError()
    except Exception:
        print("  -> caught by 'except Exception' (BAD)")
except asyncio.CancelledError:
    print("  -> NOT caught by 'except Exception' (finding's parenthetical is self-correcting)")

H("O. finding[24] SQLAlchemy sqlite round-trip of Certificate.not_after")
from app.models.certificate import Certificate as CM
import inspect
src = open(r"D:/workspace/sandbox/app/models/certificate.py", encoding="utf-8").read()
for n, ln in enumerate(src.splitlines(), 1):
    if "not_before" in ln or "not_after" in ln or "class Certificate" in ln:
        print(f"    models/certificate.py:{n}: {ln.strip()[:120]}")
from sqlalchemy import MetaData, Table, Column, create_engine
from sqlalchemy.orm import Session
coltype = CM.__table__.columns["not_after"].type
eng = create_engine("sqlite://")
md = MetaData(); tb = Table("t", md, Column("d", coltype))
md.create_all(eng)
import datetime as dt
with Session(eng) as s:
    s.execute(tb.insert().values(d=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)))
with Session(eng) as s:
    v = s.execute(tb.select()).scalar_one()
print("  stored aware UTC datetime, read back as:", type(v).__name__, repr(v), "tzinfo:", v.tzinfo)
try:
    print("  validate_certificate-style compare (now_aware > not_after):",
          dt.datetime.now(dt.timezone.utc) > v)
except TypeError as e:
    print("  compare raises", type(e).__name__ + ":", e)

H("P. live reachability recap (import graph, executed)")
import importlib
for m in ("app.services.query_rewriter", "app.services.db_access_proxy"):
    mod = importlib.import_module(m)
    print(f"  {m}: importable, module-level singleton present =",
          hasattr(mod, m.split(".")[-1]))
import app.api.sandbox_db as sb
print("  app/api/sandbox_db imports secure_duckdb:",
      "secure_duckdb" in open(sb.__file__, encoding="utf-8").read())
src_main = open(r"D:/workspace/sandbox/app/main.py", encoding="utf-8").read()
for tok in ("sandbox_db", "certificates"):
    print(f"  main.py include_router for {tok}:",
          bool(re.search(r"include_router\(" + tok + r"\.router", src_main)))
