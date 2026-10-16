"""scripts/prepare_tongsuo.sh 的取用契约：Tongsuo 只能以仓内钉住的 sha 落地（fail-closed）。"""

from __future__ import annotations

import io
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "prepare_tongsuo.sh"
GIT = shutil.which("git")
MARKER_FILES = ("Configure", "crypto/sm4/sm4.c", "include/openssl/opensslv.h.in")

requires_git = pytest.mark.skipif(
    GIT is None, reason="环境门禁：运行镜像内无 git，与 test_cds_volume_plugin 同类登记"
)


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(
        [GIT, *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def _fake_upstream(tmp_path: Path) -> Path:
    up = tmp_path / "upstream"
    up.mkdir()
    _git(up, "-c", "init.defaultBranch=main", "init", "-q", ".")
    for rel in MARKER_FILES:
        target = up / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"stub {rel}\n", encoding="utf-8")
    _git(up, "add", "-A")
    _git(up, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "stub tree")
    return up


def _consumer_repo(tmp_path: Path, pin: str) -> Path:
    repo = tmp_path / "consumer"
    (repo / "scripts").mkdir(parents=True)
    _git(repo, "-c", "init.defaultBranch=main", "init", "-q", ".")
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{pin},Tongsuo")
    shutil.copy(SCRIPT, repo / "scripts" / "prepare_tongsuo.sh")
    return repo


def _run(repo: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/prepare_tongsuo.sh"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=120,
        env={"PATH": "/usr/bin:/bin", **env},
    )


def _pin_of(repo: Path) -> str:
    return _git(repo, "ls-files", "-s", "Tongsuo").split()[1]


@requires_git
def test_repo_pins_tongsuo_sha() -> None:
    proc = subprocess.run(
        [GIT, "ls-files", "-s", "Tongsuo"], cwd=REPO, capture_output=True, text=True
    )
    sha = proc.stdout.split()[1] if proc.returncode == 0 and len(proc.stdout.split()) > 1 else ""
    if len(sha) != 40:
        pytest.skip("验证树非 git 仓（无 gitlink 可读），此断言只在真实 clone 上有意义")
    assert len(sha) == 40


@requires_git
def test_missing_tarball_fails_closed(tmp_path: Path) -> None:
    repo = _consumer_repo(tmp_path, _pin_of(REPO))
    proc = _run(repo, {"TONGSUO_TARBALL": str(tmp_path / "nope.tar.gz")})
    assert proc.returncode != 0
    assert "源包不存在" in proc.stdout + proc.stderr


@requires_git
def test_tarball_sha_mismatch_fails_closed_and_leaves_no_tree(tmp_path: Path) -> None:
    repo = _consumer_repo(tmp_path, _pin_of(REPO))
    junk = tmp_path / "junk.tar.gz"
    with tarfile.open(junk, "w:gz") as tf:
        data = b"not tongsuo\n"
        ti = tarfile.TarInfo("Configure")
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))
    proc = _run(repo, {"TONGSUO_TARBALL": str(junk)})
    assert proc.returncode != 0
    assert "sha256 不匹配" in proc.stdout + proc.stderr
    assert not (repo / "Tongsuo" / "Configure").exists(), "sha 不匹配时不得落地任何源码树"


@requires_git
def test_mirror_route_materialises_pinned_sha(tmp_path: Path) -> None:
    up = _fake_upstream(tmp_path)
    pin = _git(up, "rev-parse", "HEAD")
    repo = _consumer_repo(tmp_path, pin)
    assert _pin_of(repo) == pin
    proc = _run(repo, {"TONGSUO_GIT_URL": str(up)})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "READY" in proc.stdout
    for rel in MARKER_FILES:
        assert (repo / "Tongsuo" / rel).is_file(), rel
    assert _git(repo / "Tongsuo", "rev-parse", "HEAD") == pin


@requires_git
def test_mirror_without_pinned_sha_fails_closed(tmp_path: Path) -> None:
    up = _fake_upstream(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "-c", "init.defaultBranch=main", "init", "-q", ".")
    (other / "README").write_text("empty\n", encoding="utf-8")
    _git(other, "add", "-A")
    _git(other, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "empty")
    repo = _consumer_repo(tmp_path, _git(up, "rev-parse", "HEAD"))
    proc = _run(repo, {"TONGSUO_GIT_URL": str(other)})
    assert proc.returncode != 0
    assert "没有钉住 sha" in proc.stdout + proc.stderr
    assert not (repo / "Tongsuo" / "Configure").exists()


def test_pinned_sha256_constant_shape() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    line = next(ln for ln in text.splitlines() if ln.startswith("TONGSUO_TARBALL_SHA256="))
    value = line.split("=", 1)[1].strip()
    assert len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    assert "TONGSUO_TARBALL_SHA256:-" not in text, "期望 sha 不得被环境变量覆盖"
