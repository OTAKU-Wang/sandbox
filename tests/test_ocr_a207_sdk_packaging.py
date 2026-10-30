"""A207（OCR-10-18 队列）— SDK 打包卫生：依赖有界、`py.typed`、版本单源不回填。

三点各钉一条可失败的针：
1. 依赖区间设上限——``httpx`` 未到 1.0，0.x minor 仍可能改行为，``>=0.27`` 无上限
   会让下次 `pip install` 撞上破坏性变更；``[stream]`` 的 websocket-client 同理。
2. ``py.typed`` 标记文件必须存在且被打包配置覆盖，否则 mypy/pyright 装了包也读不到
   类型标注（本 SDK 两个模块全程 annotated，不声明纯属浪费）。
3. 版本单源（A78）：``pyproject.toml`` 的 ``[project].version`` 是唯一真值，
   ``__init__.py`` 不许回填字面 ``__version__ = "..."``（既有测试也钉着，这里从
   打包面再锁一次：pyproject 必须真的有 version）。
"""
import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk"))

SDK = Path(__file__).resolve().parents[1] / "sdk"
PYPROJECT = SDK / "pyproject.toml"
PKG = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _spec_clauses(req: str) -> list[tuple[str, str]]:
    """`(op, version)` 对，纯 stdlib 解析（不引 packaging 当新测试依赖）。

    只支持本仓库实际用到的 PEP 508 形状：``name>=x,<y``（无 extras/环境标记）。
    """
    import re

    m = re.fullmatch(
        r"([A-Za-z0-9._-]+)\s*"
        r"((?:>=|<=|==|~=|!=|>|<)\s*[\w.+-]+"
        r"(?:\s*,\s*(?:>=|<=|==|~=|!=|>|<)\s*[\w.+-]+)*)",
        req.strip(),
    )
    assert m, f"unparsable requirement: {req!r}"
    clauses: list[tuple[str, str]] = []
    for part in m.group(2).split(","):
        op, ver = re.match(
            r"\s*(>=|<=|==|~=|!=|>|<)\s*([\w.+-]+)\s*$", part
        ).groups()
        clauses.append((op, ver))
    return clauses


def _has_upper_bound(req: str) -> bool:
    return any(op in ("<", "<=") for op, _ in _spec_clauses(req))


def _has_lower_bound(req: str) -> bool:
    return any(op in (">=", ">", "==", "~=") for op, _ in _spec_clauses(req))


# ── 1. 依赖有上限 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("req", PKG["project"]["dependencies"])
def test_core_dependencies_are_bounded(req):
    assert _has_lower_bound(req), f"{req}: 缺下限"
    assert _has_upper_bound(req), f"{req}: A207 —— 无上限的依赖不许回 package"


@pytest.mark.parametrize("req", PKG["project"]["optional-dependencies"]["stream"])
def test_stream_extra_is_bounded(req):
    assert _has_lower_bound(req)
    assert _has_upper_bound(req), f"{req}: A207 —— [stream] 依赖同样要有上限"


def _allows(clauses: list[tuple[str, str]], candidate: str) -> bool:
    """candidate 版本是否落在 (op, ver) 子句集合内——纯 stdlib 数值比对。"""
    import re

    def vt(v: str) -> tuple[int, ...]:
        parts = tuple(int(p) for p in re.findall(r"\d+", v)[:3])
        return parts or (0,)

    cand = vt(candidate)
    for op, ver in clauses:
        bound = vt(ver)
        n = max(len(bound), len(cand))
        bound += (0,) * (n - len(bound))
        padded = cand + (0,) * (n - len(cand))
        verdict = {"<": padded < bound, "<=": padded <= bound,
                   ">": padded > bound, ">=": padded >= bound,
                   "==": padded == bound}.get(op)
        assert verdict is not None, f"unsupported operator in test: {op!r}"
        if not verdict:
            return False
    return True


def test_httpx_bounds_cover_current_floor_and_block_1_0():
    """性质断言（A207 口径，不钉字面量——否则 A207 的教训自己先违反）：
    现网下限 0.27.0 必须仍在区间内（保 API 面），1.0.0 必须被挡（上限活着）。
    合法的版本调整移动针不红；撤掉上限当场红。"""
    httpx_req = next(r for r in PKG["project"]["dependencies"]
                     if r.lower().startswith("httpx"))
    clauses = _spec_clauses(httpx_req)
    assert _allows(clauses, "0.27.0"), f"现网下限不再被覆盖：{httpx_req}"
    assert not _allows(clauses, "1.0.0"), f"A207：上限没挡住 httpx 1.0：{httpx_req}"


# ── 2. py.typed 存在且被 package-data 覆盖 ───────────────────────────────────


def test_py_typed_marker_exists():
    assert (SDK / "cds_sdk" / "py.typed").is_file(), "A207: 缺 py.typed 标记"


def test_py_typed_covered_by_package_data_config():
    pkg_data = PKG.get("tool", {}).get("setuptools", {}).get("package-data", {})
    assert "py.typed" in pkg_data.get("cds_sdk", []), \
        "py.typed 文件存在但没进 package-data，wheel 里会丢"


def test_py_typed_matches_annotated_surface():
    """声明有类型的诚实前提：三个模块都带 future annotations（全文件搜，
    文件头可能是长 docstring）。"""
    for mod in ("client.py", "stream.py", "_encoding.py"):
        body = (SDK / "cds_sdk" / mod).read_text(encoding="utf-8")
        assert "from __future__ import annotations" in body, mod


# ── 3. 版本单源（A78），不从打包面回填双源 ──────────────────────────────────


def test_pyproject_declares_version_exactly_once():
    import re

    raw = PYPROJECT.read_text(encoding="utf-8")
    decls = re.findall(r"(?m)^version\s*=", raw)
    assert len(decls) == 1, "A78：顶层 version 声明只许出现在 [project] 一处"
    assert PKG["project"]["version"], "pyproject 必须声明版本（A78 单源）"


def test_init_has_no_literal_version_assignment():
    src = (SDK / "cds_sdk" / "__init__.py").read_text(encoding="utf-8")
    assert '__version__ = "' not in src, \
        "A78：字面 __version__ 双来源不许回来（另一测试也从代码面钉这条）"
