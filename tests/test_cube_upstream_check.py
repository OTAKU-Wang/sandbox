"""SS-10 CB-00: upstream sync & contract watch.

Covers the CB-00 test protocol (spec §4) + tasking:
  a. generated stubs import cleanly and expose the real CubeboxMgr RPC surface
  b. _VERSION.json parses and pins the expected upstream sha
  c. cube-upstream-check.sh exits 0 at the pinned commit against the real
     upstream dir (skip-clean when the dir is absent, e.g. in CI)
  c2. contract-path set hit/miss on a synthetic upstream repo (spec §4 测试:
      契约 diff 路径集命中/未命中, pinned-sha validity)
  d. stub regeneration idempotence (same sha twice → byte-identical stubs)
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
STUBS = REPO / "app" / "_stubs" / "cube"
GEN_SCRIPT = REPO / "scripts" / "gen-cube-stubs.sh"
CHECK_SCRIPT = REPO / "scripts" / "cube-upstream-check.sh"
VENV_PY = REPO / ".venv" / "bin" / "python"
PIN = "9b75d33907ed7336b1eef73a935ae2d1990d9b35"

# The 15 CubeboxMgr RPCs, read from the pinned proto (spec §2.2).
CUBEBOX_RPCS = (
    "Create",
    "Destroy",
    "List",
    "Update",
    "Exec",
    "AppSnapshot",
    "CommitSandbox",
    "RollbackSandbox",
    "CleanupTemplate",
    "ListSandboxSnapshots",
    "ListLocalSnapshots",
    "GetLocalSnapshot",
    "GetStorageMetrics",
    "InspectStorageVolumes",
    "CleanupOrphanStorageFiles",
)


def _upstream_dir() -> Path:
    """Same source precedence as the scripts: CUBE_MIRROR, then CUBE_UPSTREAM_DIR, then default."""
    mirror = os.environ.get("CUBE_MIRROR", "/opt/cds/cube-mirror.git")
    if Path(mirror).is_dir():
        return Path(mirror)
    return Path(os.environ.get("CUBE_UPSTREAM_DIR", str(Path.home() / "Documents" / "workspace" / "CubeSandbox")))


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


def _pin_present(up: Path) -> bool:
    return _git(up, "rev-parse", "--verify", "-q", f"{PIN}^{{commit}}").returncode == 0


class _MethodRecorder:
    """Stand-in gRPC channel: records full_method names bound by a Stub."""

    def __init__(self) -> None:
        self.methods: set[str] = set()

    def unary_unary(self, *args, **kwargs):
        full = kwargs.get("full_method") or (args[0] if args else "")
        self.methods.add(str(full).rsplit("/", 1)[-1])
        return lambda *a, **k: None


def _stub_methods(stub_cls) -> set:
    rec = _MethodRecorder()
    stub_cls(rec)
    return rec.methods


# ---------------------------------------------------------------- (a) stubs

def test_cubebox_stubs_import_and_rpc_surface():
    from app._stubs.cube.cubebox.v1 import cubebox_pb2, cubebox_pb2_grpc

    assert cubebox_pb2.RunCubeSandboxRequest.DESCRIPTOR.name == "RunCubeSandboxRequest"
    assert set(CUBEBOX_RPCS) <= _stub_methods(cubebox_pb2_grpc.CubeboxMgrStub)


def test_volumeplugin_stubs_import_and_rpc_surface():
    from app._stubs.cube.volumeplugin.v1 import volumeplugin_pb2, volumeplugin_pb2_grpc

    assert volumeplugin_pb2.AttachRequest.DESCRIPTOR.name == "AttachRequest"
    assert {"Attach", "Detach"} <= _stub_methods(volumeplugin_pb2_grpc.VolumePluginServiceStub)
    assert {"Create", "Destroy"} <= _stub_methods(volumeplugin_pb2_grpc.VolumeControllerServiceStub)


# -------------------------------------------------------------- (b) pinning

def test_version_json_parses_and_pins_expected_sha():
    data = json.loads((STUBS / "_VERSION.json").read_text(encoding="utf-8"))
    assert data["commit"] == PIN
    assert data["git_sha"] == PIN
    assert data["generator"] == "gen-cube-stubs.sh"
    for key in ("tag", "generated_at", "proto_hash", "stub_hash"):
        assert key in data and data[key], key


# ------------------------------------------------- (c) check script, real dir

@pytest.mark.parametrize("name", ["real-upstream"], ids=["real-upstream"])
def test_upstream_check_exits_0_at_pin(name):
    up = _upstream_dir()
    if not up.is_dir():
        pytest.skip(f"upstream repo absent in this environment: {up}")
    if not _pin_present(up):
        pytest.skip(f"pinned sha {PIN} not present in {up}")
    if _git(up, "rev-parse", "HEAD").stdout.strip() != PIN:
        pytest.skip("upstream HEAD != pin; this test asserts the at-pin behaviour only")
    r = subprocess.run(
        [str(CHECK_SCRIPT)],
        cwd=REPO,
        capture_output=True,
        text=True,
        env={**os.environ, "CUBE_UPSTREAM_DIR": str(up)},
        timeout=120,
    )
    assert r.returncode == 0, f"exit={r.returncode}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    assert "no drift" in r.stdout


# ------------------------------------- (c2) contract path set hit / miss (spec §4)

_GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "cb00", "GIT_AUTHOR_EMAIL": "cb00@test",
    "GIT_COMMITTER_NAME": "cb00", "GIT_COMMITTER_EMAIL": "cb00@test",
}


def _sh(cwd: Path, *args: str) -> None:
    r = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, env={**os.environ, **_GIT_IDENTITY})
    assert r.returncode == 0, f"{args}\n{r.stderr}"


def _make_upstream(tmp_path: Path) -> Path:
    up = tmp_path / "upstream"
    (up / "pkgs" / "proto" / "services" / "cubebox" / "v1").mkdir(parents=True)
    (up / "pkgs" / "proto" / "services" / "cubebox" / "v1" / "cubebox.proto").write_text(
        'syntax = "proto3";\nmessage P {}\nmessage R {}\nservice A { rpc Ping(P) returns (R); }\n'
    )
    (up / "README.md").write_text("base\n")
    _sh(up, "git", "init", "-q")
    _sh(up, "git", "add", "-A")
    _sh(up, "git", "commit", "-qm", "base")
    return up


def _head(up: Path) -> str:
    return _git(up, "rev-parse", "HEAD").stdout.strip()


def _commit(up: Path, msg: str) -> None:
    _sh(up, "git", "add", "-A")
    _sh(up, "git", "commit", "-qm", msg)


def _run_check(up: Path, pin: str | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "CUBE_UPSTREAM_DIR": str(up)}
    if pin:
        env["CUBE_PIN"] = pin
    return subprocess.run([str(CHECK_SCRIPT)], cwd=REPO, capture_output=True, text=True, env=env, timeout=120)


def test_check_contract_path_hit_is_drift(tmp_path):
    up = _make_upstream(tmp_path)
    base = _head(up)
    (up / "pkgs" / "proto" / "services" / "cubebox" / "v1" / "cubebox.proto").write_text(
        'syntax = "proto3";\nmessage P {}\nmessage R {}\nservice A { rpc Ping(P) returns (R); rpc Extra(P) returns (R); }\n'
    )
    _commit(up, "contract change")
    r = _run_check(up, pin=base)
    assert r.returncode == 1, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    assert "CONTRACT DRIFT" in r.stdout
    assert "pkgs/proto/services/cubebox/v1/cubebox.proto" in r.stdout


def test_check_non_contract_change_only_is_exit_2(tmp_path):
    up = _make_upstream(tmp_path)
    base = _head(up)
    (up / "README.md").write_text("unrelated change\n")
    _commit(up, "non-contract change")
    r = _run_check(up, pin=base)
    assert r.returncode == 2, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    assert "non-contract drift only" in r.stdout


def test_check_missing_pin_is_error(tmp_path):
    up = _make_upstream(tmp_path)
    r = _run_check(up, pin="deadbeef" * 5)
    assert r.returncode == 3, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"


# ------------------------------------------------ (d) regeneration idempotence

def test_stub_hash_matches_version_json():
    """Spec CB-01 step 1 门禁：stub 文件哈希 vs _VERSION.json 一致，不一致即红。

    Recomputes the generator's stub_hash (sorted relpath + sha256 of each
    file, _VERSION.json excluded, __pycache__ ignored) and requires the
    committed tree to match the recorded value — any unregenerated manual
    edit of a stub turns this red.
    """
    import hashlib

    version = json.loads((STUBS / "_VERSION.json").read_text(encoding="utf-8"))
    recorded = version.get("stub_hash")
    assert recorded, "_VERSION.json missing stub_hash"

    buf = b""
    files = [
        p for p in STUBS.rglob("*")
        if p.is_file() and p.name != "_VERSION.json" and "__pycache__" not in p.parts
    ]
    for p in sorted(files, key=lambda p: p.relative_to(STUBS).as_posix()):
        rel = p.relative_to(STUBS).as_posix().encode()
        buf += rel + b"\0" + hashlib.sha256(p.read_bytes()).hexdigest().encode() + b"\0"
    computed = "sha256:" + hashlib.sha256(buf).hexdigest()
    assert computed == recorded, (
        f"stub hash drift: tree={computed} recorded={recorded} — "
        f"rerun scripts/gen-cube-stubs.sh or restore the pinned stubs"
    )

def test_regen_is_idempotent(tmp_path):
    if not VENV_PY.exists():
        pytest.skip("no .venv in this environment (stub regeneration needs grpcio-tools)")
    up = _upstream_dir()
    if not up.is_dir() or not _pin_present(up):
        pytest.skip(f"upstream repo absent or pin missing: {up}")
    snap = tmp_path / "stubs"
    shutil.copytree(STUBS, snap, ignore=shutil.ignore_patterns("__pycache__"))
    try:
        r = subprocess.run(["bash", str(GEN_SCRIPT)], cwd=REPO, capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
        for p in sorted(snap.rglob("*")):
            if not p.is_file() or p.name == "_VERSION.json":
                continue  # dirs carry no content; generated_at timestamps each run, stubs must be stable
            rel = p.relative_to(snap)
            cur = STUBS / rel
            assert cur.is_file(), f"missing after regen: {rel}"
            assert cur.read_bytes() == p.read_bytes(), f"byte diff after regen: {rel}"
    finally:
        # restore the exact committed state — this task must not leave the tree modified
        shutil.rmtree(STUBS)
        shutil.copytree(snap, STUBS)
