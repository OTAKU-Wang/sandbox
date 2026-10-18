import os, sys
sys.path.insert(0, r"D:/workspace/sandbox")
os.chdir(r"C:/Users/清彧/AppData/Local/Temp/ocr-cds")
os.environ.setdefault("CDS_DATABASE_URL", "sqlite+aiosqlite:///./_verify_tmp.db")
from app.services import certificate_service as cs
names = ["digitalSignature", "nonRepudiation", "keyEncipherment", "dataEncipherment",
         "keyAgreement", "keyCertSign", "cRLSign", "encipherOnly"]
print("### M2 KeyUsage named bits, honouring the unused-bits count")
for label, is_ca in (("is_ca=True (CA)", True), ("is_ca=False (EE, live TLCP path)", False)):
    body = cs.certificate_service._build_extensions(is_ca)
    i = body.find(b"\x55\x1d\x0f")
    unused, content = body[i + 7], body[i + 8]
    usable = 8 - unused
    bits = [names[b] for b in range(usable) if content & (0x80 >> b)]
    print(f"  {label}: BIT STRING={body[i+5:i+9].hex()} unused_bits={unused} content=0x{content:02x} -> {bits}")
print("  correct EE encoding per RFC5280 = 03 02 00 A0 -> ['digitalSignature','keyEncipherment']")

print("\n### O2 SQLAlchemy sqlite round-trip of DateTime(timezone=True)")
from app.models.certificate import Certificate as CM
from sqlalchemy import MetaData, Table, Column, create_engine
from sqlalchemy.orm import Session
import datetime as dt
eng = create_engine("sqlite://"); md = MetaData()
tb = Table("t", md, Column("d", CM.__table__.columns["not_after"].type)); md.create_all(eng)
with Session(eng) as s:
    s.execute(tb.insert().values(d=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc))); s.commit()
with Session(eng) as s:
    v = s.execute(tb.select()).scalar_one()
print("  written aware UTC datetime -> read back:", type(v).__name__, repr(v), "tzinfo:", v.tzinfo)
try:
    print("  now_aware > value ->", dt.datetime.now(dt.timezone.utc) > v)
except TypeError as e:
    print("  now_aware > value ->", type(e).__name__ + ":", e)
