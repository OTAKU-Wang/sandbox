import os, sys, uuid, datetime as dt
sys.path.insert(0, r"D:\workspace\sandbox")
os.environ.setdefault("CDS_DATABASE_URL", "sqlite+aiosqlite:///" + os.path.join(os.environ.get("TEMP","."), "cds_tz_probe.db").replace("\\","/"))
from sqlalchemy import select, create_engine
from sqlalchemy.orm import Session
import app.main  # noqa
from app.core.database import Base
from app.models.app_credential import AppCredential, CredentialStatus

db_path = os.path.join(os.environ.get("TEMP","."), "cds_tz_probe.db")
if os.path.exists(db_path): os.remove(db_path)
eng = create_engine(f"sqlite:///{db_path}")
AppCredential.__table__.create(eng)
now = dt.datetime.now(dt.timezone.utc)
with Session(eng) as s:
    c = AppCredential(id=uuid.uuid4(), app_id="app_x", app_secret_hash="h",
                      contract_id=uuid.uuid4(), consumer_id=uuid.uuid4(),
                      status=CredentialStatus.ACTIVE.value, rate_limit=1, quota_rows=1, quota_bytes=1,
                      expires_at=now + dt.timedelta(hours=1))
    s.add(c); s.commit()
with Session(eng) as s:
    c = s.execute(select(AppCredential)).scalar_one()
    print("stored col type:", AppCredential.__table__.columns["expires_at"].type)
    print("read back:", repr(c.expires_at), "tzinfo:", c.expires_at.tzinfo)
    try:
        print("compare:", c.expires_at < dt.datetime.now(dt.timezone.utc))
    except Exception as e:
        print("COMPARE RAISED:", type(e).__name__, e)
print("DDL:", __import__("sqlalchemy.schema", fromlist=["x"]).CreateTable(AppCredential.__table__).compile(dialect=__import__("sqlalchemy.dialects.sqlite.pysqlite", fromlist=["SQLiteDialect_pysqlite"]).SQLiteDialect_pysqlite()))
