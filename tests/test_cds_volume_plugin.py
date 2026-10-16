"""CB-03 CDS 加密卷插件测试 (SS-10).

三层：
(a) C helper (sm4_gcm) 二进制协议：加解密往返 + GCM tag 篡改检测（密文/tag/AAD/密钥各翻 1 字节）；
(b) Go 插件磁盘布局与生命周期：经 `ctl` CLI 子命令（无 gRPC server 依赖）+ 内嵌 mock KMS 控制面；
    at-rest 断言：密文目录内不得出现特征明文；
    fail-closed 断言：KMS 宕机 Attach 拒绝 / 密文篡改 Attach 拒绝 / DEK 吊销失败 Destroy 保留数据。
(c) private_data / plugin-volume-sources 注解解析（spec §2.4）。

构建产物不存在时自动构建；构建失败则如实 skip（环境门禁，CI 登记）。
"""
import json
import os
import signal
import socket
import struct
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO / "cds-volume-plugin"
HELPER = PLUGIN_DIR / "sm4gcm" / "sm4_gcm"
TONGSUO_PREFIX = PLUGIN_DIR / "tongsuo-build"
PLUGIN_BIN = PLUGIN_DIR / "bin" / "cds-volume-plugin"


# ── 构建门禁 ──────────────────────────────────────────────────────────────

def _build(cmd: list[str], cwd: Path) -> bool:
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=600)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@pytest.fixture(scope="session")
def helper_path() -> Path:
    if not HELPER.exists() or not TONGSUO_PREFIX.exists():
        assert _build(["make", "sm4_gcm"], PLUGIN_DIR / "sm4gcm"), (
            "sm4_gcm helper build failed (Tongsuo prefix missing or compile error)"
        )
    assert HELPER.exists()
    return HELPER


@pytest.fixture(scope="session")
def plugin_bin() -> Path:
    if not PLUGIN_BIN.exists():
        assert _build(["make", "local"], PLUGIN_DIR), "cds-volume-plugin native build failed"
    assert PLUGIN_BIN.exists()
    return PLUGIN_BIN


def run_bin(binary: Path, *args: str, env: dict | None = None, data: bytes = b"") -> subprocess.CompletedProcess:
    e = os.environ.copy()
    if env:
        e.update(env)
    r = subprocess.run(
        [str(binary), *args], input=data, capture_output=True, timeout=120, env=e
    )
    # binary surface: decode ourselves (text mode would clash with bytes stdin)
    r.stdout = r.stdout.decode("utf-8", "replace")
    r.stderr = r.stderr.decode("utf-8", "replace")
    return r


# ── helper 二进制协议（与 sm4gcm/sm4_gcm.h 一致）──────────────────────────

def helper_frame(key: bytes, nonce: bytes, aad: bytes, tag: bytes | None = None, body: bytes = b"") -> bytes:
    b = key + nonce + struct.pack(">I", len(aad)) + aad
    if tag is not None:
        b += tag
    return b + body


def call_helper(helper: Path, op: str, blob: bytes) -> subprocess.CompletedProcess:
    return subprocess.run([str(helper), op], input=blob, capture_output=True, timeout=60)


def test_helper_roundtrip_recovers_plaintext(helper_path):
    import os as _os
    key, nonce = _os.urandom(16), _os.urandom(12)
    aad = b"cds-data-session-1"
    pt = ("CDS-DATA-CANARY-0042 " * 257).encode() + _os.urandom(1024)
    enc = call_helper(helper_path, "encrypt", helper_frame(key, nonce, aad, body=pt))
    assert enc.returncode == 0, enc.stderr
    tag, ct = enc.stdout[:16], enc.stdout[16:]
    assert len(ct) == len(pt) and ct != pt
    dec = call_helper(helper_path, "decrypt", helper_frame(key, nonce, aad, tag=tag, body=ct))
    assert dec.returncode == 0, dec.stderr
    assert dec.stdout == pt


def test_helper_tamper_ciphertext_detected(helper_path):
    import os as _os
    key, nonce = _os.urandom(16), _os.urandom(12)
    aad = b"vol-tamper-ct"
    pt = b"sensitive-payload-1234567890"
    enc = call_helper(helper_path, "encrypt", helper_frame(key, nonce, aad, body=pt))
    tag, ct = enc.stdout[:16], bytearray(enc.stdout[16:])
    ct[3] ^= 0x01  # flip 1 byte of ciphertext → GCM tag must reject
    dec = call_helper(helper_path, "decrypt", helper_frame(key, nonce, aad, tag=tag, body=bytes(ct)))
    assert dec.returncode == 3
    assert dec.stdout == b""  # never emit unauthenticated plaintext


def test_helper_tamper_tag_detected(helper_path):
    import os as _os
    key, nonce = _os.urandom(16), _os.urandom(12)
    aad = b"vol-tamper-tag"
    pt = b"sensitive-payload-abcdef"
    enc = call_helper(helper_path, "encrypt", helper_frame(key, nonce, aad, body=pt))
    tag = bytearray(enc.stdout[:16]); ct = enc.stdout[16:]
    tag[0] ^= 0x01
    dec = call_helper(helper_path, "decrypt", helper_frame(key, nonce, aad, tag=bytes(tag), body=ct))
    assert dec.returncode == 3
    assert dec.stdout == b""


def test_helper_tamper_aad_detected(helper_path):
    import os as _os
    key, nonce = _os.urandom(16), _os.urandom(12)
    aad = b"vol-aad-1"
    pt = b"aad-binding-matters"
    enc = call_helper(helper_path, "encrypt", helper_frame(key, nonce, aad, body=pt))
    tag, ct = enc.stdout[:16], enc.stdout[16:]
    dec = call_helper(helper_path, "decrypt", helper_frame(key, nonce, aad + b"x", tag=tag, body=ct))
    assert dec.returncode == 3


def test_helper_wrong_key_rejected(helper_path):
    import os as _os
    key, nonce = _os.urandom(16), _os.urandom(12)
    aad = b"vol-key"
    pt = b"key-binding"
    enc = call_helper(helper_path, "encrypt", helper_frame(key, nonce, aad, body=pt))
    tag, ct = enc.stdout[:16], enc.stdout[16:]
    dec = call_helper(helper_path, "decrypt", helper_frame(_os.urandom(16), nonce, aad, tag=tag, body=ct))
    assert dec.returncode == 3


def test_helper_usage_error_exit(helper_path):
    r = call_helper(helper_path, "frobnicate", b"")
    assert r.returncode == 1


# ── mock CDS 控制面 KMS（文档化契约路径）────────────────────────────────

class MockKMS:
    def __init__(self):
        self.store: dict[str, str] = {}
        self.n = 0
        self.unwrap_fail = 0
        self.delete_fail = 0
        self.requests: list[str] = []
        srv = self  # closure ref

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self):
                n = int(self.headers.get("Content-Length", 0))
                return json.loads(self.rfile.read(n) or b"{}")

            def do_POST(self):
                srv.requests.append("POST " + self.path)
                if self.path == "/api/v1/kms/volume-keys":
                    d = self._body()
                    srv.n += 1
                    ref = f"vol-dek-ref-{srv.n}"
                    srv.store[ref] = d["dek_hex"]
                    self._json(201, {"key_id": ref})
                elif self.path.endswith("/distribute"):
                    ref = self.path[len("/api/v1/kms/volume-keys/"):-len("/distribute")]
                    if srv.unwrap_fail > 0:
                        srv.unwrap_fail -= 1
                        self._json(500, {"detail": "injected failure"})
                    elif ref not in srv.store:
                        self._json(404, {"detail": "unknown ref"})
                    else:
                        self._json(200, {"key_hex": srv.store[ref]})
                else:
                    self._json(404, {"detail": "unexpected"})

            def do_DELETE(self):
                srv.requests.append("DELETE " + self.path)
                ref = self.path[len("/api/v1/kms/volume-keys/"):]
                if srv.delete_fail > 0:
                    self._json(500, {"detail": "injected failure"})
                elif ref in srv.store:
                    del srv.store[ref]
                    self.send_response(204)
                    self.end_headers()
                else:
                    self._json(404, {"detail": "unknown ref"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture()
def mock_kms():
    m = MockKMS()
    yield m
    m.close()


def plugin_env(kms: MockKMS, tmp: Path, backend: str = "tongsuo", helper: Path | None = None) -> dict:
    e = {
        "CDS_VOLUME_PLUGIN_KMS_BASE_URL": kms.base_url,
        "CDS_VOLUME_PLUGIN_KMS_SERVICE_TOKEN": "test-token",
        "CDS_VOLUME_PLUGIN_VOLUME_ROOT": str(tmp / "volumes"),
        "CDS_SM4_AEAD_BACKEND": backend,
        "CDS_VOLUME_PLUGIN_NODE_ID": "pytest-node",
    }
    if backend == "tongsuo":
        e["CDS_VOLUME_PLUGIN_SM4_HELPER"] = str(helper)
    return e


# ── (b) 磁盘布局与生命周期（CLI 表面，无 gRPC）────────────────────────────

def test_plugin_selftest(plugin_bin, helper_path):
    r = run_bin(plugin_bin, "selftest", env={
        "CDS_SM4_AEAD_BACKEND": "tongsuo",
        "CDS_VOLUME_PLUGIN_SM4_HELPER": str(helper_path),
        "CDS_VOLUME_PLUGIN_VOLUME_ROOT": "/tmp/cds-vp-selftest",
    })
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert "tongsuo" in out["cipher"]


def test_plugin_full_lifecycle_on_disk_layout(plugin_bin, helper_path, mock_kms, tmp_path):
    env = plugin_env(mock_kms, tmp_path, helper=helper_path)
    base_dir = tmp_path / "stage"
    base_dir.mkdir()
    vid = "cds-data-sess-1"
    canary = ("CDS-RAW-DATA-CANARY-7788 " * 512).encode()
    data_file = tmp_path / "data.bin"
    data_file.write_bytes(canary)

    r = run_bin(plugin_bin, "ctl", "create", "--volume-id", vid, "--name", "cds-data-sess-1", env=env)
    assert r.returncode == 0, r.stderr
    created = json.loads(r.stdout)
    pd_obj = json.loads(created["private_data"])
    assert "session_dek_ref" in pd_obj
    assert len(created["private_data"]) <= 1024

    vdir = tmp_path / "volumes" / vid
    assert (vdir / "manifest.json").exists()
    assert not (vdir / "data.ct").exists()  # 无数据前不得有密文文件

    r = run_bin(plugin_bin, "ctl", "put", "--volume-id", vid, "--data-file", str(data_file), env=env)
    assert r.returncode == 0, r.stderr
    put = json.loads(r.stdout)
    assert put["data_size_bytes"] == len(canary)

    # 布局断言：密文 + nonce(12B) + tag(16B) 并存
    ct = (vdir / "data.ct").read_bytes()
    nonce = (vdir / "data.nonce").read_bytes()
    tag = (vdir / "data.tag").read_bytes()
    assert len(nonce) == 12 and len(tag) == 16 and len(ct) == len(canary)
    assert canary[:24] not in ct, "特征明文泄漏到 at-rest 密文目录"
    mani = json.loads((vdir / "manifest.json").read_text())
    assert mani["has_data"] is True
    assert mani["aad"] == "cds-data-sess-1"  # AAD = 卷名/label

    # Attach：host_path 必须位于 volume_base_dir 内，明文与写入一致
    r = run_bin(plugin_bin, "ctl", "attach", "--volume-id", vid, "--base-dir", str(base_dir),
                "--sandbox-id", "sbx-1", "--private-data", created["private_data"], env=env)
    assert r.returncode == 0, r.stderr
    att = json.loads(r.stdout)
    host_path = Path(att["host_path"])
    assert str(host_path).startswith(str(base_dir) + os.sep), "host_path 越出 volume_base_dir"
    staged = host_path / "data"
    assert staged.exists() and staged.read_bytes() == canary

    # Detach ref_count=0：明文库 shred
    r = run_bin(plugin_bin, "ctl", "detach", "--volume-id", vid, "--base-dir", str(base_dir),
                "--ref-count", "0", env=env)
    assert r.returncode == 0, r.stderr
    assert not staged.exists(), "ref_count=0 后明文必须擦除"

    # Destroy：KMS 吊销 + 密文树清除
    r = run_bin(plugin_bin, "ctl", "destroy", "--volume-id", vid, env=env)
    assert r.returncode == 0, r.stderr
    assert not vdir.exists()
    assert mock_kms.store == {}


def test_attach_fail_closed_when_kms_down(plugin_bin, helper_path, mock_kms, tmp_path):
    env = plugin_env(mock_kms, tmp_path, helper=helper_path)
    base_dir = tmp_path / "stage"
    base_dir.mkdir()
    vid = "cds-data-kms"
    r = run_bin(plugin_bin, "ctl", "create", "--volume-id", vid, env=env)
    assert r.returncode == 0, r.stderr
    pd = json.loads(r.stdout)["private_data"]
    data_file = tmp_path / "d.bin"
    data_file.write_bytes(b"kms-outage-payload")
    r = run_bin(plugin_bin, "ctl", "put", "--volume-id", vid, "--data-file", str(data_file), env=env)
    assert r.returncode == 0, r.stderr

    mock_kms.unwrap_fail = 5  # 模拟 KMS 故障
    r = run_bin(plugin_bin, "ctl", "attach", "--volume-id", vid, "--base-dir", str(base_dir),
                "--sandbox-id", "sbx", "--private-data", pd, env=env)
    assert r.returncode != 0, "KMS 故障时 Attach 必须 fail-closed"
    assert not (base_dir / f"cds-{vid}" / "data").exists(), "失败 Attach 不得留下明文库"


def test_attach_rejects_empty_private_data(plugin_bin, helper_path, mock_kms, tmp_path):
    env = plugin_env(mock_kms, tmp_path, helper=helper_path)
    base_dir = tmp_path / "stage"
    base_dir.mkdir()
    vid = "cds-data-nopd"
    r = run_bin(plugin_bin, "ctl", "create", "--volume-id", vid, env=env)
    assert r.returncode == 0, r.stderr
    r = run_bin(plugin_bin, "ctl", "attach", "--volume-id", vid, "--base-dir", str(base_dir),
                "--sandbox-id", "sbx", "--private-data", "", env=env)
    assert r.returncode != 0, "空 private_data（fallback 路径）必须拒绝"


def test_attach_rejects_tampered_ciphertext(plugin_bin, helper_path, mock_kms, tmp_path):
    env = plugin_env(mock_kms, tmp_path, helper=helper_path)
    base_dir = tmp_path / "stage"
    base_dir.mkdir()
    vid = "cds-data-tamper"
    r = run_bin(plugin_bin, "ctl", "create", "--volume-id", vid, env=env)
    assert r.returncode == 0, r.stderr
    pd = json.loads(r.stdout)["private_data"]
    data_file = tmp_path / "d.bin"
    data_file.write_bytes(b"integrity-matters-here-0123456789")
    r = run_bin(plugin_bin, "ctl", "put", "--volume-id", vid, "--data-file", str(data_file), env=env)
    assert r.returncode == 0, r.stderr

    ct_path = tmp_path / "volumes" / vid / "data.ct"
    ct = bytearray(ct_path.read_bytes())
    ct[0] ^= 0x01
    ct_path.write_bytes(bytes(ct))
    r = run_bin(plugin_bin, "ctl", "attach", "--volume-id", vid, "--base-dir", str(base_dir),
                "--sandbox-id", "sbx", "--private-data", pd, env=env)
    assert r.returncode != 0, "密文篡改后 Attach 必须拒绝（GCM 完整性 / fail-closed）"
    assert not (base_dir / f"cds-{vid}" / "data").exists()


def test_destroy_retains_data_when_dek_revoke_fails(plugin_bin, helper_path, mock_kms, tmp_path):
    env = plugin_env(mock_kms, tmp_path, helper=helper_path)
    vid = "cds-data-revoke"
    r = run_bin(plugin_bin, "ctl", "create", "--volume-id", vid, env=env)
    assert r.returncode == 0, r.stderr
    data_file = tmp_path / "d.bin"
    data_file.write_bytes(b"retained-until-revoked")
    r = run_bin(plugin_bin, "ctl", "put", "--volume-id", vid, "--data-file", str(data_file), env=env)
    assert r.returncode == 0, r.stderr

    mock_kms.delete_fail = 3
    r = run_bin(plugin_bin, "ctl", "destroy", "--volume-id", vid, env=env)
    assert r.returncode != 0, "DEK 吊销失败时 Destroy 必须失败"
    vdir = tmp_path / "volumes" / vid
    assert vdir.exists() and (vdir / "data.ct").exists(), "吊销失败时密文树必须保留（DEK 仍存活）"
    assert len(mock_kms.store) == 1


# ── (c) private_data / 注解解析（spec §2.4）───────────────────────────────

def test_parse_annotation_cds_entry(plugin_bin):
    annotation = json.dumps([
        {"name": "cds-data-s1", "driver": "cds-encrypted",
         "private_data": json.dumps({"session_dek_ref": "vol-dek-ref-1"})},
        {"name": "results", "driver": "local", "private_data": ""},
    ])
    r = run_bin(plugin_bin, "parse-annotation", annotation)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert len(out) == 2
    cds = [e for e in out if e["is_cds_driver"]]
    assert len(cds) == 1
    assert cds[0]["name"] == "cds-data-s1"
    assert cds[0]["parsed_data"]["session_dek_ref"] == "vol-dek-ref-1"


def test_parse_annotation_rejects_bad_payload(plugin_bin):
    assert run_bin(plugin_bin, "parse-annotation", '{"name":"x"}').returncode != 0
    assert run_bin(plugin_bin, "parse-annotation", '[{"name":"x"}]').returncode != 0
    assert run_bin(plugin_bin, "parse-annotation", "not json").returncode != 0


def test_private_data_matches_spec_shape(plugin_bin, helper_path, mock_kms, tmp_path):
    """Create 返回的 private_data = {session_dek_ref: <KMS wrapped key id>}（spec CB-03 步骤2）。"""
    env = plugin_env(mock_kms, tmp_path, helper=helper_path)
    r = run_bin(plugin_bin, "ctl", "create", "--volume-id", "cds-data-shape", env=env)
    assert r.returncode == 0, r.stderr
    pd = json.loads(json.loads(r.stdout)["private_data"])
    assert set(pd.keys()) == {"session_dek_ref"}
    assert pd["session_dek_ref"].startswith("vol-dek-ref-")
    assert len(json.loads(r.stdout)["private_data"].encode()) <= 1024
