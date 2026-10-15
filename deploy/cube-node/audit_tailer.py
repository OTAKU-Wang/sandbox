#!/usr/bin/env python3
"""CDS cube-node egress audit tailer (SS-10 CB-06, CDS component 3).

Tails the CubeEgress forced-point audit JSONL (spec SS-10 §2.7) and pushes
complete lines to the CDS control plane (POST /internal/audit/egress) with
at-least-once delivery.  The CDS endpoint dedups on the idempotency key
``node_id + file_name + offset + line_no`` (PG table ``cube_egress_dedup``),
so crash/restart re-reads are absorbed there.

Red line 5 (audit must not be dropped, spec SS-10 §1.4):
  * push failure  -> bounded local disk queue (AUDIT_TAILER_QUEUE_MAX lines);
  * queue full    -> CRITICAL log + overflow marker file + WRITE-STOP
                     (no further source reads; NO silent drop);
  * queue drained below high-water -> marker removed, reading resumes.

The tailer is dependency-light: stdlib only for the core tail/queue logic;
httpx is required only by the HTTP pusher (imported lazily).  The systemd
unit (deploy/cube-node/audit-tailer.service, delivered by CB-02) reads the
AUDIT_TAILER_* keys from /etc/cds/audit-tailer.env.

mTLS is fail-closed: if any of the AUDIT_TAILER_TLS_{CERT,KEY,CA} files is
missing/empty the tailer refuses to start (exit code 2) — an audit link
without its transport credential must not silently fall back to plaintext.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

logger = logging.getLogger("cds-audit-tailer")

DEFAULT_AUDIT_DIR = "/data/log/cube-egress"
DEFAULT_AUDIT_FILE = "access.jsonl"
DEFAULT_OFFSET_FILE = "/var/lib/cds/audit-tailer/offset.json"
DEFAULT_QUEUE_FILE = "/var/lib/cds/audit-tailer/queue.jsonl"
DEFAULT_QUEUE_MAX = 10000
DEFAULT_PUSH_BATCH_MAX = 200
DEFAULT_POLL_INTERVAL_SEC = 0.5
DEFAULT_OVERFLOW_MARKER = "/var/lib/cds/cube-node/audit-tailer-OVERFLOW"
# resume reading once the queue drains below this fraction of its bound
RESUME_FRACTION = 0.8


class TailerConfigError(RuntimeError):
    """Fail-closed startup condition (missing required env / mTLS material)."""


class Pusher(Protocol):
    """Push interface — inject a fake in tests; the production
    implementation is :class:`HttpPusher` (mTLS httpx)."""

    def push_batch(self, node_id: str, file_name: str, entries: list[dict]) -> bool:
        """POST one batch.  Return True on 2xx; False on any failure."""
        ...


class HttpPusher:
    """Production pusher: mTLS + service-token POST to CDS
    /internal/audit/egress (httpx).

    The CDS endpoint is fail-closed on BOTH transports: mTLS client cert AND
    ``Authorization: Bearer <CDS_CUBE_AUDIT_SERVICE_TOKEN>``
    (``app/core/deps.py::verify_cube_audit_token``).  A pusher without the
    token would get 401 on every batch, so the token is required."""

    def __init__(
        self,
        endpoint: str,
        cert: str,
        key: str,
        ca: str,
        token: str,
        timeout: float = 10.0,
    ):
        import httpx  # lazy: node needs httpx only for the live pusher

        if not token:
            raise TailerConfigError(
                "service token required — /internal/audit/egress rejects every "
                "request without Authorization: Bearer (fail-closed)"
            )
        self._endpoint = endpoint.rstrip("/")
        self._token = token
        self._client = httpx.Client(
            verify=ca,
            cert=(cert, key),
            timeout=timeout,
        )

    def push_batch(self, node_id: str, file_name: str, entries: list[dict]) -> bool:
        url = f"{self._endpoint}/internal/audit/egress"
        try:
            resp = self._client.post(
                url,
                json={"node_id": node_id, "file_name": file_name, "entries": entries},
                headers={"Authorization": f"Bearer {self._token}"},
            )
            if 200 <= resp.status_code < 300:
                return True
            logger.error(
                "CDS push rejected: status=%s body=%s",
                resp.status_code, resp.text[:300],
            )
        except Exception as e:  # network/TLS failure — caller queues the lines
            logger.error("CDS push failed: %s", e)
        return False

    def close(self) -> None:
        self._client.close()


class DiskQueue:
    """Bounded append-only JSONL queue on local node disk (red line 5).

    Each line is one entry: {"file_name", "offset", "line_no", "record"}.
    """

    def __init__(self, path: str, max_lines: int):
        self._path = Path(path)
        self._max = max(1, int(max_lines))
        if not self._path.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.touch()

    def __len__(self) -> int:
        with self._path.open("r", encoding="utf-8") as f:
            return sum(1 for _ in f)

    def enqueue(self, entries: list[dict]) -> bool:
        """Append entries.  Returns False when the bound is hit (overflow)."""
        room = self._max - len(self)
        if room <= 0:
            return False
        with self._path.open("a", encoding="utf-8") as f:
            for e in entries[:room]:
                f.write(json.dumps(e, ensure_ascii=False, default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())
        if room < len(entries):
            return False
        return True

    def dequeue(self, up_to: int) -> list[dict]:
        """Remove and return up to `up_to` oldest entries (atomic rewrite)."""
        with self._path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        if not lines:
            return []
        take = lines[:up_to]
        rest = lines[up_to:]
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), prefix=".queue-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.writelines(rest)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        return [json.loads(line) for line in take]


class Watermark:
    """Persistent read watermark (JSON file, atomic rewrite).

    ``offset``        — source offset successfully delivered (direct or via queue);
    ``pending_offset`` — source offset read so far (delivered or queued).
    After a crash, entries in [offset, pending_offset) are in the disk queue
    (or, if we died between queue-append and watermark-write, re-read from the
    source — the CDS idempotency key absorbs the duplicate).
    ``inode`` / ``file_name`` identify the current file; an inode change
    (log rotation) starts a fresh file identity for the idempotency key.
    """

    def __init__(self, path: str):
        self._path = Path(path)
        self.inode: int | None = None
        self.file_name: str = ""
        self.offset: int = 0
        self.pending_offset: int = 0

    def load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self.inode = data.get("inode")
                self.file_name = data.get("file_name", "")
                self.offset = int(data.get("offset", 0))
                self.pending_offset = int(data.get("pending_offset", self.offset))
            except Exception as e:
                logger.critical(
                    "watermark file %s unreadable (%s) — starting from offset 0; "
                    "re-read lines are absorbed by the CDS idempotency key", self._path, e,
                )
                self.reset()

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), prefix=".offset-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "inode": self.inode,
                        "file_name": self.file_name,
                        "offset": self.offset,
                        "pending_offset": self.pending_offset,
                    },
                    f,
                )
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def reset(self) -> None:
        self.inode = None
        self.file_name = ""
        self.offset = 0
        self.pending_offset = 0


def idem_file_name(inode: int) -> str:
    """Idempotency file_name: rotation-safe (includes the source inode)."""
    return f"{DEFAULT_AUDIT_FILE}#ino-{inode}"


@dataclass
class TailState:
    buffer: bytes = b""          # trailing partial line carried across reads
    line_no: int = 0             # 1-based line counter of the current file
    overflow: bool = False       # write-stop engaged (red line 5)

    def reset_file_counters(self) -> None:
        self.buffer = b""
        self.line_no = 0


class AuditTail:
    """Core tail loop body — pure of time/network: a pusher is injected."""

    def __init__(
        self,
        node_id: str,
        audit_file: Path,
        watermark: Watermark,
        queue: DiskQueue,
        pusher: Pusher,
        overflow_marker: Path,
        batch_max: int = DEFAULT_PUSH_BATCH_MAX,
    ):
        self.node_id = node_id
        self.audit_file = audit_file
        self.watermark = watermark
        self.queue = queue
        self.pusher = pusher
        self.overflow_marker = overflow_marker
        self.batch_max = batch_max
        self.state = TailState()

    # -- source reading ---------------------------------------------------

    def _current_file(self) -> tuple[int, int] | None:
        try:
            st = self.audit_file.stat()
        except FileNotFoundError:
            return None
        return st.st_ino, st.st_size

    def _sync_file_identity(self) -> bool:
        """Adopt the current file; detect rotation by inode change.

        Returns True when a fresh file was adopted (counters reset).
        """
        cur = self._current_file()
        if cur is None:
            return False
        inode, size = cur
        name = idem_file_name(inode)
        if self.watermark.inode != inode:
            self.watermark.inode = inode
            self.watermark.file_name = name
            self.watermark.offset = 0
            self.watermark.pending_offset = 0
            self.state.reset_file_counters()
            self.watermark.save()
            logger.warning("audit file (re)adopted: %s inode=%d from offset 0", name, inode)
            return True
        if size < self.watermark.pending_offset:
            # truncated in place (not rotation) — restart this file identity
            self.watermark.offset = 0
            self.watermark.pending_offset = 0
            self.state.reset_file_counters()
            self.watermark.save()
            logger.warning("audit file truncated — re-reading from 0 (dedup absorbs)")
            return True
        return False

    def read_new_lines(self) -> list[dict]:
        """Read complete lines newly appended since pending_offset.

        Returns [{"offset","line_no","record","read_end"}]; a line split
        across reads is held in the buffer until its newline arrives.
        Malformed JSON is NOT dropped — it is pushed verbatim under
        record["_raw"].
        """
        out: list[dict] = []
        if self.state.overflow:
            return out  # write-stop: no new tail reads (red line 5)
        cur = self._current_file()
        if cur is None:
            return out
        inode, size = cur
        if size <= self.watermark.pending_offset:
            return out
        try:
            with self.audit_file.open("rb") as f:
                f.seek(self.watermark.pending_offset)
                chunk = f.read(size - self.watermark.pending_offset)
        except OSError as e:
            logger.error("audit file read failed: %s", e)
            return out
        data = self.state.buffer + chunk
        last_nl = data.rfind(b"\n")
        if last_nl < 0:
            self.state.buffer = data  # still no complete line
            self.watermark.pending_offset += len(chunk)  # bytes are in the buffer now
            self.watermark.save()
            return out
        complete = data[: last_nl + 1]
        self.state.buffer = data[last_nl + 1:]
        offset = self.watermark.pending_offset
        for line in complete.splitlines(keepends=True):
            start = offset
            offset += len(line)
            raw = line.rstrip(b"\n").decode("utf-8", errors="replace").strip()
            if not raw:
                continue
            self.state.line_no += 1
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                record = {"_raw": raw}  # never drop an audit line
            out.append(
                {
                    "offset": start,
                    "line_no": self.state.line_no,
                    "record": record,
                    # byte boundary of this read: a delivered batch safely
                    # advances the watermark to its max read_end (line start
                    # offsets alone cannot express the end of the last line)
                    "read_end": offset,
                }
            )
        self.watermark.pending_offset = offset
        self.watermark.save()
        return out

    # -- delivery ----------------------------------------------------------

    def _drain_queue(self) -> None:
        if not len(self.queue):
            return
        batch = self.queue.dequeue(self.batch_max)
        if not batch:
            return
        # group by file_name: the endpoint payload carries one file_name per batch
        groups: dict[str, list[dict]] = {}
        for e in batch:
            groups.setdefault(e["file_name"], []).append(e)
        failed: list[dict] = []
        for file_name, entries in groups.items():
            if self.pusher.push_batch(self.node_id, file_name, entries):
                if self.watermark.file_name == file_name:
                    new_off = max(e.get("read_end", e["offset"]) for e in entries)
                    if new_off > self.watermark.offset:
                        self.watermark.offset = new_off
            else:
                failed.extend(entries)  # never drop queued lines (red line 5)
        if failed:
            # put the batch back (space exists: we just dequeued at least these)
            self.queue.enqueue(failed)
            return
        self.watermark.save()
        if self.state.overflow and len(self.queue) <= self.queue._max * RESUME_FRACTION:
            self._end_overflow()

    def _push_direct(self, entries: list[dict]) -> None:
        if not entries:
            return
        file_name = self.watermark.file_name
        if self.pusher.push_batch(self.node_id, file_name, entries):
            self.watermark.offset = max(e.get("read_end", e["offset"]) for e in entries)
            self.watermark.save()
            return
        # push failure -> bounded disk queue (never dropped, red line 5)
        queued = self.queue.enqueue(
            [
                {
                    "file_name": file_name,
                    "offset": e["offset"],
                    "line_no": e["line_no"],
                    "read_end": e.get("read_end", e["offset"]),
                    "record": e["record"],
                }
                for e in entries
            ]
        )
        if not queued:
            self._enter_overflow()

    def _enter_overflow(self) -> None:
        self.state.overflow = True
        self.overflow_marker.parent.mkdir(parents=True, exist_ok=True)
        self.overflow_marker.write_text(
            json.dumps(
                {
                    "node_id": self.node_id,
                    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "queue_max": self.queue._max,
                }
            )
        )
        logger.critical(
            "AUDIT QUEUE OVERFLOW: local queue at bound (%d lines); write-stop "
            "engaged (no new tail reads, no drops). marker=%s — resuming when "
            "the queue drains. red line 5: audit must not be lost.",
            self.queue._max,
            self.overflow_marker,
        )

    def _rewind_source(self) -> None:
        """Re-read the undelivered tail after an overflow: the lines between
        watermark.offset and the old pending_offset were read but never
        delivered and may not have fit in the queue.  Re-reading them is
        safe — the CDS idempotency key absorbs the overlap with lines that
        were delivered via the queue."""
        self.watermark.pending_offset = self.watermark.offset
        self.state.buffer = b""
        self.state.line_no = 0
        # recount line_no up to the rewind point so keys stay stable
        try:
            with self.audit_file.open("rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(0)
                data = f.read(self.watermark.offset if self.watermark.offset <= size else size)
                self.state.line_no = data.count(b"\n")
        except OSError:
            pass
        self.watermark.save()

    def _end_overflow(self) -> None:
        self.state.overflow = False
        try:
            self.overflow_marker.unlink()
        except FileNotFoundError:
            pass
        self._rewind_source()
        logger.warning(
            "audit queue drained below high-water; write-stop lifted, "
            "resuming tail reads (marker removed)"
        )

    # -- one loop iteration --------------------------------------------------

    def step(self) -> None:
        """One iteration: drain the queue first, then tail + direct push."""
        if self.state.overflow:
            self._drain_queue()  # keep working; reading stays stopped
            return
        if self._current_file() is None:
            return
        self._sync_file_identity()
        self._drain_queue()
        entries = self.read_new_lines()
        self._push_direct(entries)


def _env(key: str, default: str | None = None) -> str | None:
    v = os.environ.get(key, default)
    return v if v not in (None, "") else None


def build_from_env(pusher: Pusher | None = None) -> AuditTail:
    """Build the tailer from AUDIT_TAILER_* env vars.  Fail-closed:
    missing required keys or mTLS material raise TailerConfigError."""
    node_id = _env("AUDIT_TAILER_NODE_ID")
    endpoint = _env("AUDIT_TAILER_CDS_ENDPOINT")
    cert = _env("AUDIT_TAILER_TLS_CERT")
    key = _env("AUDIT_TAILER_TLS_KEY")
    ca = _env("AUDIT_TAILER_TLS_CA")
    token = _env("AUDIT_TAILER_SERVICE_TOKEN")
    missing = [
        name
        for name, val in (
            ("AUDIT_TAILER_NODE_ID", node_id),
            ("AUDIT_TAILER_CDS_ENDPOINT", endpoint),
            ("AUDIT_TAILER_TLS_CERT", cert),
            ("AUDIT_TAILER_TLS_KEY", key),
            ("AUDIT_TAILER_TLS_CA", ca),
            ("AUDIT_TAILER_SERVICE_TOKEN", token),
        )
        if not val
    ]
    if missing:
        raise TailerConfigError(f"missing required env: {', '.join(missing)}")
    for name, path in (("TLS_CERT", cert), ("TLS_KEY", key), ("TLS_CA", ca)):
        if not Path(path).is_file() or Path(path).stat().st_size == 0:
            raise TailerConfigError(
                f"{name} file missing or empty: {path} — refusing to start "
                "(fail-closed: no plaintext fallback for the audit link)"
            )
    audit_dir = Path(_env("AUDIT_TAILER_AUDIT_DIR") or DEFAULT_AUDIT_DIR)
    queue_max = int(_env("AUDIT_TAILER_QUEUE_MAX") or DEFAULT_QUEUE_MAX)
    offset_file = _env("AUDIT_TAILER_OFFSET_FILE") or DEFAULT_OFFSET_FILE
    queue_file = _env("AUDIT_TAILER_QUEUE_FILE") or DEFAULT_QUEUE_FILE
    marker = Path(
        _env("AUDIT_TAILER_OVERFLOW_MARKER") or DEFAULT_OVERFLOW_MARKER
    )
    if pusher is None:
        pusher = HttpPusher(endpoint, cert, key, ca, token or "")
    watermark = Watermark(offset_file)
    watermark.load()
    queue = DiskQueue(queue_file, queue_max)
    return AuditTail(
        node_id=node_id,
        audit_file=audit_dir / DEFAULT_AUDIT_FILE,
        watermark=watermark,
        queue=queue,
        pusher=pusher,
        overflow_marker=marker,
    )


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    try:
        tail = build_from_env()
    except TailerConfigError as e:
        logger.critical("refusing to start: %s", e)
        return 2
    except Exception as e:
        # fail-closed: any startup failure (missing httpx, bad TLS material,
        # unwritable paths) exits 2 — systemd Restart + loud CRITICAL, never
        # a silently absent audit link
        logger.critical("refusing to start: %s: %s", type(e).__name__, e)
        return 2

    stop = {"flag": False}

    def _sig(_signum, _frame):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    poll = float(_env("AUDIT_TAILER_POLL_INTERVAL_SEC") or DEFAULT_POLL_INTERVAL_SEC)
    logger.info(
        "audit tailer starting: node_id=%s file=%s push->%s",
        tail.node_id, tail.audit_file, _env("AUDIT_TAILER_CDS_ENDPOINT"),
    )
    while not stop["flag"]:
        try:
            tail.step()
        except Exception as e:
            # never let the loop die — systemd Restart + watermark make it
            # self-healing; the failure is loud, not silent
            logger.exception("tail step failed: %s", e)
        if not stop["flag"]:
            time.sleep(poll)
    if isinstance(tail.pusher, HttpPusher):
        tail.pusher.close()
    logger.info("audit tailer stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
