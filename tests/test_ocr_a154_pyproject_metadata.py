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
import tomllib
from pathlib import Path
from typing import Any

SDK_DIR = Path(__file__).resolve().parents[1] / "sdk"
PYPROJECT = SDK_DIR / "pyproject.toml"


def _project() -> dict[str, Any]:
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)["project"]


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
    assert _project().get("optional-dependencies") == {"stream": ["websocket-client>=1.6"]}


def test_readme_file_actually_ships():
    readme = _project().get("readme")
    assert isinstance(readme, str)
    assert (SDK_DIR / readme).is_file(), f"{SDK_DIR / readme} 不存在"
