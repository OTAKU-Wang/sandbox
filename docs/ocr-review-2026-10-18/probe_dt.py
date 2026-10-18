
import asyncio, sys
from datetime import datetime, timezone
from sqlalchemy import DateTime, Integer, Column, select
from sqlalchemy.orm import declarative_base
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
Base = declarative_base()
class Row(Base):
    __tablename__ = "t"
    id = Column(Integer, primary_key=True)
    expires_at = Column(DateTime)          # same type as AppCredential.expires_at
async def main():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    S = async_sessionmaker(eng, expire_on_commit=False)
    async with S() as s:
        s.add(Row(id=1, expires_at=datetime.now(timezone.utc)))   # aware write, as app/api/gateway.py does
        await s.commit()
        r = (await s.execute(select(Row))).scalar_one()
        print("read back tzinfo:", r.expires_at.tzinfo)
        try:
            print("compare:", r.expires_at < datetime.now(timezone.utc))
        except TypeError as e:
            print("TypeError:", e)
asyncio.run(main())
