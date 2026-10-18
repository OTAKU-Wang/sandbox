
import os, tempfile, time, gc
import app.core.database as db
from sqlalchemy.ext.asyncio import create_async_engine as real_create
from sqlalchemy import text

tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "bridge.db")
url = "sqlite+aiosqlite:///" + path.replace(os.sep, "/")
db.settings.DATABASE_URL = url
print("url", url)
b = db.SyncDbBridge()

async def work():
    async with db.sync_bridge_session() as s:
        return (await s.execute(text("SELECT 1"))).scalar_one()

print("run:", b.run(work()))
eng = b._engine
print("pool status:", eng.pool.status())
print("checkedout:", eng.pool.checkedout(), "size:", getattr(eng.pool,'size',lambda:'?')())
b.close()
print("after close: loop", b._loop, "thread", b._thread)
print("pool status after:", eng.pool.status())
for attempt in range(6):
    gc.collect()
    try:
        os.remove(path); print("removed on attempt", attempt); break
    except PermissionError as e:
        print("attempt", attempt, "locked"); time.sleep(0.25)
