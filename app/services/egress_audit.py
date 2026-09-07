"""W13: egress audit — JSONL trail for sandbox network decisions.

CubeSandbox C3 alignment: every outbound judgment (allow/deny/policy
lifecycle) leaves a structured, secret-free JSONL record. Writes go through
a bounded asyncio queue drained by a single writer coroutine, so the data
plane never blocks on disk and an overflowing queue drops records with a
visible counter instead of stalling sandboxes.
"""
import asyncio
import base64
import hashlib
import json
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

from app.core.config import get_settings

_QUEUE_MAX = 1000
_SECRET_PARAM = re.compile(r"((?:token|key|secret|password|sig)=)[^&\s]+", re.IGNORECASE)

_dropped_count = 0
_queue: asyncio.Queue | None = None
_writer_task: asyncio.Task | None = None
_lock = threading.Lock()


def redact_query_string(value: str) -> str:
    """Mask values of secret-looking query parameters (keys stay readable)."""
    return _SECRET_PARAM.sub(r"\1***", value)


class EgressEvent(dict):
    pass


def _serialize(event: EgressEvent) -> str:
    return json.dumps(event, ensure_ascii=False, default=str)


def _audit_cipher() -> "SM4Cipher | None":
    """Stable audit-encryption key: SHA-256 of the configured master secret."""
    settings = get_settings()
    if not settings.EGRESS_AUDIT_ENCRYPTION_ENABLED:
        return None
    from app.utils.crypto import SM4Cipher

    secret = settings.AUDIT_ENCRYPTION_KEY or settings.JWT_SECRET_KEY
    return SM4Cipher(key=hashlib.sha256(secret.encode("utf-8")).digest())


def encrypt_line(line: str, cipher: "SM4Cipher") -> str:
    ciphertext, nonce, tag = cipher.encrypt_gcm(line.encode("utf-8"))
    return base64.b64encode(nonce + tag + ciphertext).decode("ascii")


def decrypt_line(blob: str, cipher: "SM4Cipher") -> str:
    raw = base64.b64decode(blob.encode("ascii"))
    nonce, tag, ciphertext = raw[:12], raw[12:28], raw[28:]
    return cipher.decrypt_gcm(ciphertext, nonce, tag).decode("utf-8")


async def _writer_loop(queue: asyncio.Queue, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_running_loop()
    file = await loop.run_in_executor(None, lambda: path.open("a", encoding="utf-8"))
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            try:
                line = _serialize(event)
                cipher = _audit_cipher()
                if cipher is not None:
                    line = encrypt_line(line, cipher)
                await loop.run_in_executor(None, lambda l=line: file.write(l + "\n"))
                await loop.run_in_executor(None, file.flush)
            except Exception as e:
                logger.warning("[EgressAudit] write failed: %s", e)
    except asyncio.CancelledError:
        pass
    finally:
        # Best-effort drain on shutdown
        try:
            while True:
                event = queue.get_nowait()
                if event is None:
                    break
                line = _serialize(event)
                cipher = _audit_cipher()
                if cipher is not None:
                    line = encrypt_line(line, cipher)
                file.write(line + "\n")
        except asyncio.QueueEmpty:
            pass
        except Exception as e:
            logger.warning("[EgressAudit] final drain failed: %s", e)
        try:
            file.close()
        except Exception:
            pass


def _ensure_writer() -> asyncio.Queue | None:
    global _queue, _writer_task
    from app.core.config import get_settings

    settings = get_settings()
    if not settings.EGRESS_AUDIT_ENABLED:
        return None
    with _lock:
        if _queue is None:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return None
            _queue = asyncio.Queue(maxsize=_QUEUE_MAX)
            path = Path(settings.EGRESS_AUDIT_LOG_PATH)
            _writer_task = asyncio.get_running_loop().create_task(_writer_loop(_queue, path))
    return _queue


def log_egress_event(
    *,
    session_id: str,
    event_type: str,
    method: str | None = None,
    host: str | None = None,
    path: str | None = None,
    verdict: str,
    rule_id: str | None = None,
    status_code: int | None = None,
    credential_used: str | None = None,
    detail: dict | None = None,
) -> bool:
    """Queue one egress audit record. Never raises, never blocks.

    Returns True when the record was queued, False when dropped (queue full
    or auditing disabled). Dropped records increment a counter surfaced in
    the next record's detail.
    """
    global _dropped_count
    queue = _ensure_writer()
    if queue is None:
        return False
    event = EgressEvent({
        "ts": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "event_type": event_type,   # access | security_event | tls_handshake
        "method": method,
        "host": host,
        "path": redact_query_string(path) if path else None,
        "verdict": verdict,          # allow | deny | policy_applied | policy_removed
        "rule_id": rule_id,
        "status_code": status_code,
        # W13 honesty rule: only credential IDs are recorded, never values.
        "credential_used": credential_used,
        "detail": detail or {},
    })
    try:
        queue.put_nowait(event)
        return True
    except asyncio.QueueFull:
        _dropped_count += 1
        logger.warning("[EgressAudit] queue full — record dropped (total dropped=%d)", _dropped_count)
        return False


async def shutdown_egress_audit() -> None:
    """Flush pending records and stop the writer (app shutdown hook)."""
    global _queue, _writer_task
    if _queue is not None and _writer_task is not None:
        try:
            _queue.put_nowait(None)
            await asyncio.wait_for(_writer_task, timeout=5)
        except Exception as e:
            logger.warning("[EgressAudit] shutdown drain issue: %s", e)
        _writer_task = None
        _queue = None


def dropped_count() -> int:
    return _dropped_count


def read_egress_records(session_id: str, *, limit: int = 50, since: str | None = None) -> list[dict]:
    """Tail the JSONL file for one session, newest first."""
    from app.core.config import get_settings

    path = Path(get_settings().EGRESS_AUDIT_LOG_PATH)
    if not path.exists():
        return []
    records: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    cipher = _audit_cipher()
                    if cipher is not None:
                        try:
                            line = decrypt_line(line, cipher)
                        except Exception:
                            logger.warning("[EgressAudit] encrypted record failed to decrypt, skipping")
                            continue
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("session_id") != session_id:
                    continue
                if since and str(record.get("ts", "")) <= since:
                    continue
                records.append(record)
    except OSError as e:
        logger.warning("[EgressAudit] read failed: %s", e)
        return []
    records.reverse()
    return records[:limit]
