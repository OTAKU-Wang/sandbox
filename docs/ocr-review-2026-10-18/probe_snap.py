import os, tempfile, pathlib
os.environ["CDS_SESSION_MAX_SNAPSHOTS"] = "0"
os.environ.setdefault("CDS_DATABASE_URL", "sqlite+aiosqlite:///./ci.db")
from app.core import config as cfg
cfg.get_settings.cache_clear()
from app.services import session_snapshots as ss
ws = pathlib.Path(tempfile.mkdtemp(prefix="snaptest-"))
(ws / "a.txt").write_text("hello", encoding="utf-8")
try:
    print("created:", ss.create_snapshot(ws, "s-1")["snapshot_id"])
except Exception as e:
    print("RAISED:", type(e).__name__, e)
print("files in ws:", [p.name for p in ws.iterdir()])
