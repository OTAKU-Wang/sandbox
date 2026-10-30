"""P1a-8① (cr §4-9「grpcio>=1.60 低于生成码 import 期硬校验」) — gencode 兼容声明机器钉。

`app/_stubs/cube` 的 gencode 在 **import 期** 双重硬校验：
- `*_pb2_grpc.py`: `GRPC_GENERATED_VERSION` + `first_version_is_lower(GRPC_VERSION, ...)`
  ⇒ 装到低于该版本的 grpcio 时 `import app._stubs` 直接 RuntimeError；
- `*_pb2.py`: `ValidateProtobufRuntimeVersion(Domain.PUBLIC, 7, 35, 1, ...)`
  ⇒ 运行时 protobuf 必须同 major 且 >= gencode 版本（AGENTS 记过真事故：系统
  python 的 7.35.0 撞仓内 7.35.1 gencode，alembic 在 import app._stubs 处抛
  Protobuf VersionError 假失败）。

旧 requirements 下限 `grpcio>=1.60.0` 允许装出一个 import 即炸的运行时，且
protobuf 完全未钉（grpcio 1.84 起不再硬依赖它）。本文件把「兼容性声明」升级为
机器钉：requirements.txt 的下限必须覆盖 gencode 的 import 期硬校验，且两侧
（所有 pb2 文件之间、gencode 注释 vs Validate 实参）必须一致——重新生成 stub 或
改下限任何一侧漂移都会在这里变红，而不是等部署机上 import 炸。
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STUBS = REPO / "app" / "_stubs" / "cube"
REQUIREMENTS = REPO / "requirements.txt"


def _vt(s: str) -> tuple[int, ...]:
    """点分数字版本 → 可比较元组（1.84.0 < 1.100.0，字典序会骗人）。"""
    return tuple(int(p) for p in s.split("."))


def _gencode_grpc_versions() -> set[str]:
    out = set()
    files = list(STUBS.rglob("*_pb2_grpc.py"))
    assert files, "app/_stubs/cube 下找不到 pb2_grpc —— 桩树被移动？同步更新本针"
    for f in files:
        m = re.search(r"^GRPC_GENERATED_VERSION = '([\d.]+)'", f.read_text(encoding="utf-8"), re.M)
        assert m, f"{f}: gencode 缺 GRPC_GENERATED_VERSION —— grpc 插件格式变了，本针需同步"
        out.add(m.group(1))
    return out


def _gencode_protobuf_versions() -> set[tuple[int, int, int]]:
    out = set()
    files = list(STUBS.rglob("*_pb2.py"))
    assert files
    for f in files:
        text = f.read_text(encoding="utf-8")
        # import 期真正生效的校验（Domain.PUBLIC, major, minor, patch）
        m = re.search(
            r"ValidateProtobufRuntimeVersion\(\s*_runtime_version\.Domain\.PUBLIC,\s*(\d+),\s*(\d+),\s*(\d+),",
            text,
        )
        assert m, f"{f}: 缺 ValidateProtobufRuntimeVersion —— 本针需同步"
        ver = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        out.add(ver)
        # 注释版本只作交叉核对（注释会随 grpc 插件重生成保留）
        c = re.search(r"# Protobuf Python Version: (\d+)\.(\d+)\.(\d+)", text)
        if c:
            assert ver == (int(c.group(1)), int(c.group(2)), int(c.group(3))), (
                f"{f}: Validate 实参 {ver} 与注释 {(c.group(1), c.group(2), c.group(3))} 不一致"
            )
    return out


def _req_lower_bound(pkg: str) -> tuple[str, str | None]:
    """requirements.txt 中 pkg 的 (>=下限, <上限|None)；无 pin 直接 fail。"""
    for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if re.match(rf"^{pkg}[=<>!~]", line, re.I):
            low = re.search(r">=\s*([\d.]+)", line)
            assert low, f"requirements.txt: {pkg} 无 >= 下限，兼容声明不完整"
            high = re.search(r"<\s*([\d.]+)", line)
            return low.group(1), high.group(1) if high else None
    raise AssertionError(
        f"requirements.txt 未声明 {pkg} —— gencode import 期硬校验要求显式钉住它"
    )


def test_gencode_versions_are_uniform():
    """两套硬校验各自必须全树一致，否则兼容窗口按最高者取。"""
    assert len(_gencode_grpc_versions()) == 1, "pb2_grpc 的 GRPC_GENERATED_VERSION 不一致"
    assert len(_gencode_protobuf_versions()) == 1, "pb2 的 protobuf 校验版本不一致"


def test_requirements_grpcio_lower_bound_covers_gencode():
    """①核心：装了下限版本就必须能 import 桩树——下限 >= 全体 GRPC_GENERATED_VERSION。"""
    gen = max(_vt(v) for v in _gencode_grpc_versions())
    lb, _ = _req_lower_bound("grpcio")
    assert _vt(lb) >= gen, (
        f"grpcio 下限 {lb} 低于 gencode import 期硬校验 {'.'.join(map(str, gen))}"
        "（旧值 1.60.0 即 cr §4-9 NIT：装出来就炸）"
    )


def test_requirements_protobuf_window_contains_gencode():
    """①另一半：protobuf 窗口必须容纳 gencode 版本且锁 major（PUBLIC 域规则：
    major 必须相等、minor 不得倒退；7.35.0 撞 7.35.1 的事故不得重演）。"""
    (gmaj, gmin, gpat), = _gencode_protobuf_versions()
    lb, ub = _req_lower_bound("protobuf")
    assert _vt(lb) <= (gmaj, gmin, gpat), f"protobuf 下限 {lb} 高于 gencode 需求，装不出可用运行时"
    assert _vt(lb)[0] == gmaj, f"protobuf 下限 {lb} major 与 gencode {gmaj} 不一致"
    assert ub is not None and _vt(ub)[0] == gmaj + 1, (
        f"protobuf 上限应锁到下一个 major（<{gmaj + 1}），实为 {ub} —— PUBLIC 域 major 漂移 import 即炸"
    )
    assert _vt(ub) > (gmaj, gmin, gpat)


def test_installed_runtime_satisfies_gencode():
    """活体核对：本环境真装了的 grpc/protobuf 必须满足 import 期校验（未装则跳过，
    桩树 import 本身在 CI 的其它用例里必跑，这里只补一条可读的兼容读数）。"""
    import importlib.util

    if importlib.util.find_spec("grpc") is None or importlib.util.find_spec("google.protobuf") is None:
        import pytest

        pytest.skip("grpcio/protobuf not installed in this env")
    import grpc
    from google.protobuf import __version__ as pb_ver

    gen = max(_vt(v) for v in _gencode_grpc_versions())
    assert _vt(grpc.__version__.split("+")[0]) >= gen, f"installed grpc {grpc.__version__} < gencode {gen}"
    (gmaj, gmin, gpat), = _gencode_protobuf_versions()
    assert _vt(pb_ver.split("+")[0])[:3] >= (gmaj, gmin, gpat) and _vt(pb_ver)[0] == gmaj, (
        f"installed protobuf {pb_ver} 不满足 gencode {(gmaj, gmin, gpat)}"
    )
