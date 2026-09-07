"""W8: keyset (cursor) pagination for list endpoints.

Cursor = base64url("<created_at iso>|<id>"). Ordering is fixed to
``created_at DESC, id DESC`` so traversal is stable under concurrent inserts
and tolerant of mid-traversal deletes (keyset pages never rescan).

Endpoints keep their existing offset envelope for legacy callers; cursor mode
is opt-in via the ``cursor`` query parameter and reports the next page in the
``X-Next-Cursor`` response header.
"""
import base64
import calendar
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, tuple_


def encode_cursor(created_at: datetime, item_id: uuid.UUID | str) -> str:
    ts = created_at.isoformat()
    raw = f"{ts}|{item_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        ts_str, _, id_str = raw.rpartition("|")
        ts = datetime.fromisoformat(ts_str)
        item_id = uuid.UUID(id_str)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Invalid pagination cursor") from exc
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts, item_id


async def paginate_keyset(db, model, *, cursor: str | None, limit: int, where=None):
    """Fetch one keyset page ordered by (created_at DESC, id DESC).

    Returns (rows, next_cursor | None). next_cursor is present only when a
    further page exists (limit+1 lookahead).
    """
    stmt = select(model)
    if where is not None:
        stmt = stmt.where(where)
    if cursor:
        ts, last_id = decode_cursor(cursor)
        if db.bind.dialect.name == "sqlite":
            stored_epoch = func.strftime("%s", model.created_at)
            cursor_epoch = str(int(calendar.timegm(ts.utctimetuple())))
            stmt = stmt.where(tuple_(stored_epoch, model.id) < tuple_(cursor_epoch, last_id))
        else:
            stmt = stmt.where(tuple_(model.created_at, model.id) < tuple_(ts, last_id))
    stmt = stmt.order_by(model.created_at.desc(), model.id.desc()).limit(limit + 1)

    rows = (await db.execute(stmt)).scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id) if has_more and rows else None
    return rows, next_cursor
