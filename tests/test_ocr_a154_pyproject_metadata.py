"""A154 — `readme` written into `[project.optional-dependencies]` becomes a
ghost extra (medium, build metadata).

Ledger (docs/ocr-review-2026-10-18/ocr-verified-findings.md ### A154): in
`sdk/pyproject.toml` the key `readme = "README.md"` sat inside
`[project.optional-dependencies]`, where every value must be a PEP 508
requirement *array*. Consequences the ledger asserts as certain:
  * no long_description in the built metadata (no README on PyPI / internal index),
  * downstream sees a fake `cds-sdk[readme]` extra.
Fix per ledger: move `readme` up into `[project]`.

These assertions are pure TOML reads (tomllib), no build backend is invoked —
the ledger itself refuses to claim "the build fails", so neither do we.
"""
import re
import tomllib
from pathlib import Path
from typing import Any

SDK_DIR = Path(__file__).resolve().parents[1] / "sdk"
PYPROJECT = SDK_DIR / "pyproject.toml"


def _project() -> dict[str, Any]:
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)["project"]


def _requirement(req: str) -> tuple[str, set[tuple[str, str]]]:
    """(name, {(op, version), …})——纯 stdlib PEP 508 解析，只覆盖 name>=x,<y 形状。"""
    m = re.fullmatch(
        r"([A-Za-z0-9._-]+)\s*"
        r"((?:>=|<=|==|~=|!=|>|<)\s*[\w.+-]+"
        r"(?:\s*,\s*(?:>=|<=|==|~=|!=|>|<)\s*[\w.+-]+)*)",
        req.strip(),
    )
    assert m, f"unparsable requirement: {req!r}"
    clauses = set()
    for part in m.group(2).split(","):
        op, ver = re.match(r"\s*(>=|<=|==|~=|!=|>|<)\s*([\w.+-]+)\s*$", part).groups()
        clauses.add((op, ver))
    return m.group(1), clauses


def test_readme_is_project_metadata_not_an_extra():
    project = _project()
    assert project.get("readme") == "README.md"
    extras = project.get("optional-dependencies", {})
    assert "readme" not in extras, (
        "readme 仍写在 [project.optional-dependencies] 下：它会变成一个假的 "
        f"cds-sdk[readme] extra，且 [project] 里没有 readme（A154）：{extras!r}"
    )


def test_every_extra_value_is_a_requirement_array():
    """PEP 621: every entry of optional-dependencies is an ARRAY of PEP 508 strings."""
    extras = _project().get("optional-dependencies", {})
    assert extras, "optional-dependencies 不应为空（stream extra 是 PZ-11② 的功能）"
    for name, value in extras.items():
        assert isinstance(value, list), (
            f"extra {name!r} 的取值不是数组，而是 {type(value).__name__}：非法 PEP 621 元数据"
        )
        assert all(isinstance(item, str) for item in value), (
            f"extra {name!r} 含非字符串项：{value!r}"
        )


def test_stream_extra_is_the_only_extra_and_keeps_its_requirement():
    """stream 是唯一 extra，websocket-client 在场且是**有界区间**（A207 口径）。

    A154 本体（extras 必须是需求数组、readme 不是 extra）+ A207 策略（上限）并存。
    预期从 pyproject 现值派生——旧针钉死字面量 ``websocket-client>=1.6``，
    A207 加上限后自爆；字面量不再进针，但「无上限回潮」依旧当场红。
    """
    extras = _project().get("optional-dependencies")
    assert extras is not None and set(extras) == {"stream"}, f"stream 应是唯一 extra：{extras!r}"
    reqs = extras["stream"]
    assert len(reqs) == 1, f"stream extra 应恰好声明一个依赖：{reqs!r}"
    name, clauses = _requirement(reqs[0])
    assert name.lower().replace("_", "-") == "websocket-client", f"stream 依赖易主：{reqs[0]!r}"
    assert any(op in (">=", ">", "==", "~=") for op, _ in clauses), f"websocket-client 缺下限：{reqs[0]!r}"
    assert any(op in ("<", "<=") for op, _ in clauses), (
        f"A207：websocket-client 必须带上限（挡住 2.x 破坏性变更），"
        f"现值 {reqs[0]!r} 无上限——无上限依赖不许回 package"
    )


def test_readme_file_actually_ships():
    readme = _project().get("readme")
    assert isinstance(readme, str)
    assert (SDK_DIR / readme).is_file(), f"{SDK_DIR / readme} 不存在"
