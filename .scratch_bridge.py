
import os, tempfile, time, logging, threading
logging.basicConfig(level=logging.DEBUG, format="%(name)s %(message)s")
for n in ("sqlalchemy.pool",):
    logging.getLogger(n).setLevel(logging.DEBUG)
import app.core.database as db
from sqlalchemy import text
tmp = tempfile.mkdtemp(); path = os.path.join(tmp, "bridge.db")
db.settings.DATABASE_URL = "sqlite+aiosqlite:///" + path.replace(os.sep, "/")
b = db.SyncDbBridge()
async def work():
    async with db.sync_bridge_session() as s:
        return (await s.execute(text("SELECT 1"))).scalar_one()
b.run(work())
print("### CLOSE ###")
b.close()
print("### DONE ###", [(t.name) for t in threading.enumerate() if t is not threading.main_thread()])
