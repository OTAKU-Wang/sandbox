"""SS-10 CB-06 — node audit tailer logic.

Covers: incremental read + offset watermark persistence, crash/restart
no-loss-no-duplicate (injected failing pusher + disk queue), log rotation
by inode, queue overflow → write-stop + overflow marker (red line 5),
malformed-line tolerance, fail-closed mTLS (missing cert → refuse), and the
service-token credential the control-plane endpoint requires on every batch.
"""
import importlib.util
import json
import logging
import os
import sys
from pathlib import Path

import pytest

TAILER_PATH = (
    Path(__file__).resolve().parents[1] / "deploy" / "cube-node" / "audit_tailer.py"
)


@pytest.fixture(scope="module")
def tailer_mod():
    spec = importlib.util.spec_from_file_location("audit_tailer", TAILER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["audit_tailer"] = mod
    spec.loader.exec_module(mod)
    return mod


class FakePusher:
    def __init__(self, fail_first_n: int = 0):
        self.batches: list[dict] = []
        self.calls = 0
        self.fail_remaining = fail_first_n

    def push_batch(self, node_id, file_name, entries):
        self.calls += 1
        if self.fail_remaining > 0:
            self.fail_remaining -= 1
            return False
        self.batches.append({"node_id": node_id, "file_name": file_name, "entries": entries})
        return True


def make_tailer(tailer_mod, tmp_path, pusher, queue_max=100):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir(exist_ok=True)
    wm = tailer_mod.Watermark(str(tmp_path / "offset.json"))
    wm.load()
    queue = tailer_mod.DiskQueue(str(tmp_path / "queue.jsonl"), queue_max)
    marker = tmp_path / "marker"
    return (
        tailer_mod.AuditTail(
            node_id="node-test",
            audit_file=audit_dir / "access.jsonl",
            watermark=wm,
            queue=queue,
            pusher=pusher,
            overflow_marker=marker,
            batch_max=10,
        ),
        audit_dir / "access.jsonl",
        marker,
    )


def append_lines(path: Path, lines: list[dict]):
    with path.open("a", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")


def rec(i, event="http_request"):
    return {
        "ts": f"2026-10-13T10:00:{i:02d}.000Z",
        "request_id": f"1-{i}",
        "event": event,
        "sandbox": {"src_ip": "10.0.0.5", "policy_id": "pol-1"},
        "conn": {"original_dst_ip": "93.184.216.34", "original_dst_port": 443},
        "tls": {"sni": "api.example.com", "cipher": "ECDHE-RSA-AES128-GCM-SHA256", "version": "TLSv1.3"},
        "http": {"method": "GET", "host": "api.example.com", "path": "/v1/x", "status": 200, "req_bytes": 10, "resp_bytes": 100},
        "policy": {"matched_rule": "rule-1", "decision": "allow", "duration_us": 1234},
        "credentials": None,
        "security": None,
    }


def all_pushed_keys(tailer, pusher):
    keys = []
    for b in pusher.batches:
        for e in b["entries"]:
            keys.append((b["node_id"], b["file_name"], e["offset"], e["line_no"]))
    return keys


def test_incremental_read_and_offset_persistence(tailer_mod, tmp_path):
    pusher = FakePusher()
    tail, audit_file, _ = make_tailer(tailer_mod, tmp_path, pusher)
    append_lines(audit_file, [rec(1), rec(2)])
    tail.step()
    assert len(pusher.batches) == 1
    assert [e["line_no"] for e in pusher.batches[0]["entries"]] == [1, 2]
    off1 = tail.watermark.offset
    assert off1 > 0
    # second step: nothing new
    tail.step()
    assert len(pusher.batches) == 1
    # watermark persisted to disk and survives reload
    wm2 = tailer_mod.Watermark(str(tmp_path / "offset.json"))
    wm2.load()
    assert wm2.offset == off1
    # incremental: new line picked up from the watermark
    append_lines(audit_file, [rec(3)])
    tail.step()
    assert len(pusher.batches) == 2
    assert pusher.batches[1]["entries"][0]["line_no"] == 3


def test_kill_restart_no_loss_no_duplicate(tailer_mod, tmp_path):
    pusher = FakePusher(fail_first_n=1)
    tail, audit_file, _ = make_tailer(tailer_mod, tmp_path, pusher)
    append_lines(audit_file, [rec(1), rec(2), rec(3)])
    tail.step()  # push fails -> lines go to the disk queue
    assert len(pusher.batches) == 0
    assert len(tail.queue) == 3
    assert tail.watermark.offset == 0  # delivered watermark not advanced
    # "kill": a brand-new tailer instance on the same files
    pusher2 = FakePusher()
    tail2, _, _ = make_tailer(tailer_mod, tmp_path, pusher2)
    tail2.step()
    keys = all_pushed_keys(tail2, pusher2)
    assert len(keys) == 3
    assert len(set(keys)) == 3  # no duplicates
    assert {e["line_no"] for e in pusher2.batches[0]["entries"]} == {1, 2, 3}
    # queue drained, watermark advanced, no further pushes on idle steps
    assert len(tail2.queue) == 0
    tail2.step()
    assert len(pusher2.batches) == 1


def test_partial_line_across_reads(tailer_mod, tmp_path):
    pusher = FakePusher()
    tail, audit_file, _ = make_tailer(tailer_mod, tmp_path, pusher)
    full = json.dumps(rec(1))
    with audit_file.open("w", encoding="utf-8") as f:
        f.write(full[: len(full) // 2])
    tail.step()
    assert len(pusher.batches) == 0  # held in buffer, not lost
    with audit_file.open("a", encoding="utf-8") as f:
        f.write(full[len(full) // 2 :] + "\n")
    tail.step()
    assert len(pusher.batches) == 1
    assert pusher.batches[0]["entries"][0]["record"]["event"] == "http_request"


def test_malformed_line_is_not_dropped(tailer_mod, tmp_path):
    pusher = FakePusher()
    tail, audit_file, _ = make_tailer(tailer_mod, tmp_path, pusher)
    with audit_file.open("w", encoding="utf-8") as f:
        f.write("{not json\n")
        f.write(json.dumps(rec(2)) + "\n")
    tail.step()
    entries = pusher.batches[0]["entries"]
    assert len(entries) == 2
    assert entries[0]["record"]["_raw"] == "{not json"
    assert entries[1]["record"]["event"] == "http_request"


def test_rotation_by_inode(tailer_mod, tmp_path):
    pusher = FakePusher()
    tail, audit_file, _ = make_tailer(tailer_mod, tmp_path, pusher)
    append_lines(audit_file, [rec(1)])
    tail.step()
    first_name = pusher.batches[0]["file_name"]
    assert first_name.startswith("access.jsonl#ino-")
    # rotation: 先把新文件建在原文件仍占用 inode 时（内核不可能复用同一 inode），再改名顶替
    # —— 直接 unlink+重建会在 inode 回收快的文件系统上拿回同一 inode（环境运气，非被测行为）
    inode_old = audit_file.stat().st_ino
    incoming = audit_file.with_name("access.jsonl.incoming")
    append_lines(incoming, [rec(10), rec(11)])
    assert incoming.stat().st_ino != inode_old
    audit_file.unlink()
    os.replace(incoming, audit_file)
    assert audit_file.stat().st_ino != inode_old
    tail.step()
    second = pusher.batches[1]
    assert second["file_name"] != first_name  # rotation-safe idempotency key
    assert [e["line_no"] for e in second["entries"]] == [1, 2]  # counter restarted


def test_truncated_file_restarts_without_loss(tailer_mod, tmp_path):
    pusher = FakePusher()
    tail, audit_file, _ = make_tailer(tailer_mod, tmp_path, pusher)
    append_lines(audit_file, [rec(1), rec(2)])
    tail.step()
    # in-place truncation (same inode)
    with audit_file.open("w", encoding="utf-8") as f:
        f.write(json.dumps(rec(3)) + "\n")
    tail.step()
    last = pusher.batches[-1]
    assert [e["line_no"] for e in last["entries"]] == [1]


def test_queue_overflow_write_stop_and_marker(tailer_mod, tmp_path, caplog):
    pusher = FakePusher(fail_first_n=10**9)  # CDS unreachable, permanently
    tail, audit_file, marker = make_tailer(tailer_mod, tmp_path, pusher, queue_max=3)
    append_lines(audit_file, [rec(i) for i in range(1, 8)])
    with caplog.at_level(logging.CRITICAL, logger="cds-audit-tailer"):
        tail.step()
    # bounded queue: never more than queue_max lines on disk
    assert len(tail.queue) == 3
    # overflow marker written + CRITICAL alert logged (red line 5, no silence)
    assert marker.exists()
    assert any(r.levelno == logging.CRITICAL and "AUDIT QUEUE OVERFLOW" in r.message for r in caplog.records)
    # write-stop engaged: no further source reads even after more lines arrive
    queue_len_before = len(tail.queue)
    assert tail.state.overflow is True
    append_lines(audit_file, [rec(99)])
    tail.step()
    assert len(tail.queue) == queue_len_before  # nothing new queued or pushed
    # recovery: CDS comes back -> queue drains, marker removed, read resumes
    pusher.fail_remaining = 0
    tail.step()
    assert len(tail.queue) == 0
    assert not marker.exists()
    assert not tail.state.overflow
    tail.step()  # re-reads the undelivered tail (lines 4-7) + picks up rec(99)
    pushed = all_pushed_keys(tail, pusher)
    assert ("node-test", tail.watermark.file_name, 0, 1) in pushed
    line_nos = sorted({e["line_no"] for b in pusher.batches for e in b["entries"]})
    assert line_nos == [1, 2, 3, 4, 5, 6, 7, 8]  # every line delivered exactly once
    assert len(pushed) == 8 == len(set(pushed))


def test_missing_cert_refuses_to_start(tailer_mod, monkeypatch, tmp_path):
    env = {
        "AUDIT_TAILER_NODE_ID": "n1",
        "AUDIT_TAILER_CDS_ENDPOINT": "https://cds:8000",
        "AUDIT_TAILER_TLS_CERT": str(tmp_path / "nope.crt"),
        "AUDIT_TAILER_TLS_KEY": str(tmp_path / "nope.key"),
        "AUDIT_TAILER_TLS_CA": str(tmp_path / "nope.ca"),
        "AUDIT_TAILER_SERVICE_TOKEN": "t0k",
    }
    for name, val in env.items():
        monkeypatch.setenv(name, val)
    with pytest.raises(tailer_mod.TailerConfigError):
        tailer_mod.build_from_env(pusher=FakePusher())
    # and a completely unset required key is refused too
    monkeypatch.delenv("AUDIT_TAILER_NODE_ID")
    with pytest.raises(tailer_mod.TailerConfigError):
        tailer_mod.build_from_env(pusher=FakePusher())


def test_missing_service_token_refuses_to_start(tailer_mod, monkeypatch, tmp_path):
    """The endpoint is fail-closed on the Bearer token (verify_cube_audit_token);
    starting without it would 401 every batch — so startup must refuse."""
    for name in ("crt", "key", "ca"):
        (tmp_path / f"t.{name}").write_text("PEM")
    for name, val in {
        "AUDIT_TAILER_NODE_ID": "n1",
        "AUDIT_TAILER_CDS_ENDPOINT": "https://cds:8000",
        "AUDIT_TAILER_TLS_CERT": str(tmp_path / "t.crt"),
        "AUDIT_TAILER_TLS_KEY": str(tmp_path / "t.key"),
        "AUDIT_TAILER_TLS_CA": str(tmp_path / "t.ca"),
    }.items():
        monkeypatch.setenv(name, val)
    monkeypatch.delenv("AUDIT_TAILER_SERVICE_TOKEN", raising=False)
    with pytest.raises(tailer_mod.TailerConfigError) as exc:
        tailer_mod.build_from_env(pusher=FakePusher())
    assert "AUDIT_TAILER_SERVICE_TOKEN" in str(exc.value)


class _FakeResponse:
    status_code = 200
    text = ""


def test_http_pusher_sends_bearer_token(tailer_mod, monkeypatch):
    """Every batch must carry Authorization: Bearer <service token> — mTLS
    alone is rejected 401 by POST /internal/audit/egress."""
    posts: list[dict] = []
    inits: list[dict] = []

    class _FakeClient:
        def __init__(self, **kwargs):
            inits.append(kwargs)

        def post(self, url, **kwargs):
            posts.append({"url": url, **kwargs})
            return _FakeResponse()

        def close(self):
            pass

    monkeypatch.setattr("httpx.Client", _FakeClient)
    pusher = tailer_mod.HttpPusher(
        "https://cds.internal:8443/", "/t.crt", "/t.key", "/t.ca", "s3cr3t-token"
    )
    assert pusher.push_batch("n1", "access.jsonl", [{"line_no": 1}]) is True
    assert posts[0]["url"] == "https://cds.internal:8443/internal/audit/egress"
    assert posts[0]["headers"]["Authorization"] == "Bearer s3cr3t-token"
    assert posts[0]["json"]["node_id"] == "n1"
    assert inits[0]["cert"] == ("/t.crt", "/t.key")  # mTLS material still wired


def test_http_pusher_refuses_empty_token(tailer_mod, monkeypatch):
    class _FakeClient:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr("httpx.Client", _FakeClient)
    with pytest.raises(tailer_mod.TailerConfigError):
        tailer_mod.HttpPusher("https://cds:8443", "/t.crt", "/t.key", "/t.ca", "")


def test_build_from_env_happy_path(tailer_mod, monkeypatch, tmp_path):
    for name in ("crt", "key", "ca"):
        (tmp_path / f"t.{name}").write_text("PEM")
    monkeypatch.setenv("AUDIT_TAILER_NODE_ID", "n1")
    monkeypatch.setenv("AUDIT_TAILER_CDS_ENDPOINT", "https://cds:8000")
    monkeypatch.setenv("AUDIT_TAILER_TLS_CERT", str(tmp_path / "t.crt"))
    monkeypatch.setenv("AUDIT_TAILER_TLS_KEY", str(tmp_path / "t.key"))
    monkeypatch.setenv("AUDIT_TAILER_TLS_CA", str(tmp_path / "t.ca"))
    monkeypatch.setenv("AUDIT_TAILER_SERVICE_TOKEN", "t0k")
    monkeypatch.setenv("AUDIT_TAILER_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("AUDIT_TAILER_OFFSET_FILE", str(tmp_path / "offset.json"))
    monkeypatch.setenv("AUDIT_TAILER_QUEUE_FILE", str(tmp_path / "queue.jsonl"))
    monkeypatch.setenv("AUDIT_TAILER_OVERFLOW_MARKER", str(tmp_path / "marker"))
    tail = tailer_mod.build_from_env(pusher=FakePusher())
    assert tail.node_id == "n1"
    assert tail.audit_file.name == "access.jsonl"
