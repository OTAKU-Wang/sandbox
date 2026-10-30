"""A206 — ``readme = "README.md"`` 写在 ``[project.optional-dependencies]`` 里
⇒ setuptools 配置校验直接报非法，SDK 打包（含 CI 的 ``pip install -e ./sdk``）失败。

PEP 621 规定 extras 的值必须是字符串数组；``readme`` 落进 extras 表后
``[project]`` 段就没有 readme 了。探针口径与账本一致：
``setuptools.config.pyprojecttoml.read_configuration`` 在 HEAD 版本上抛
``ValueError: ... `project.optional-dependencies.readme` must be array``。
"""
import re
import sys
import tomllib
from pathlib import Path

SDK_DIR = Path(__file__).resolve().parents[1] / "sdk"
PYPROJECT = SDK_DIR / "pyproject.toml"

sys.path.insert(0, str(SDK_DIR))


def _pyproject():
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)


def _requirement(req: str) -> tuple[str, set[tuple[str, str]]]:
    """(name, {(op, version), …})——纯 stdlib PEP 508 解析，只覆盖 name>=x,<y 形状。

    预期从 pyproject 现值派生而非钉字面量：setuptools 会把子句**重排序**
    （``>=1.6,<2.0`` 写进 METADATA 变 ``<2.0,>=1.6``），字面比对本来就脆，
    A207 加上限后更是当场红——集合比对两头都不怕。
    """
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


def _stream_requirement_from_pyproject() -> tuple[str, set[tuple[str, str]]]:
    reqs = _pyproject()["project"]["optional-dependencies"]["stream"]
    assert len(reqs) == 1, f"stream extra 应恰好声明一个依赖：{reqs!r}"
    return _requirement(reqs[0])


def test_readme_is_project_metadata_not_an_extra():
    project = _pyproject()["project"]
    extras = project.get("optional-dependencies", {})

    assert "readme" not in extras, (
        "readme 落在 [project.optional-dependencies] 里 ⇒ 它变成了一个名为 readme 的 "
        "extra，而 [project] 没有 readme"
    )
    assert project.get("readme") == "README.md"
    assert (SDK_DIR / str(project["readme"])).is_file(), "readme 指向的文件必须存在"


def test_every_extra_value_is_a_string_array():
    """PEP 621：extras 的每个值都是字符串数组（HEAD 上 readme 是裸字符串）。"""
    extras = _pyproject()["project"].get("optional-dependencies", {})
    assert extras, "stream extra 本来就该存在"
    for name, value in extras.items():
        assert isinstance(value, list), f"extra {name!r} 不是数组，而是 {value!r}"
        assert all(isinstance(item, str) for item in value), f"extra {name!r} 含非字符串项"
    assert set(extras) == {"stream"}


def test_setuptools_accepts_the_packaging_config():
    from setuptools.config.pyprojecttoml import read_configuration

    config = read_configuration(str(PYPROJECT))
    project = config["project"]

    extras = project["optional-dependencies"]
    assert set(extras) == {"stream"}, f"stream 应是唯一 extra：{extras!r}"
    name, clauses = _stream_requirement_from_pyproject()
    assert name.lower().replace("_", "-") == "websocket-client", f"stream 依赖易主：{name!r}"
    # A154 结构 + A207 有界性，两头都锁；预期从 pyproject 现值派生，setuptools 解析后
    # 必须与 pyproject 声明逐子句一致（read_configuration 不改区间）。
    parsed_name, parsed_clauses = _requirement(extras["stream"][0])
    assert parsed_name == name and parsed_clauses == clauses, (
        f"setuptools 解析结果与 pyproject 声明不一致：{parsed_clauses} != {clauses}"
    )
    assert any(op in (">=", ">", "==", "~=") for op, _ in clauses), f"websocket-client 缺下限：{sorted(clauses)}"
    assert any(op in ("<", "<=") for op, _ in clauses), (
        f"A207：stream 依赖必须带上限，现值 {extras['stream'][0]!r} 无上限——无上限依赖不许回 package"
    )
    assert project.get("readme"), "readme 必须被解析成项目元数据"


def test_build_backend_metadata_step_succeeds(tmp_path, monkeypatch):
    """CI 跑的是 ``pip install -e ./sdk``（.github/workflows/ci.yml）；build 后端以
    当前工目录为源目录，所以先 chdir 到 sdk/ 再调元数据钩子，复现同一条会红的步骤。"""
    from setuptools import build_meta

    monkeypatch.chdir(SDK_DIR)
    out = build_meta.prepare_metadata_for_build_wheel(str(tmp_path))
    assert out.endswith(".dist-info")
    text = (tmp_path / out / "METADATA").read_text(encoding="utf-8")

    assert "Metadata-Version:" in text
    assert "Provides-Extra: stream" in text
    # Requires-Dist 断言从 pyproject 现值派生：setuptools 会重排子句
    # （`>=1.6,<2.0` → `<2.0,>=1.6`），钉任何字面量（旧 'websocket-client>=1.6'
    # 或新 '>=1.6,<2.0'）都是脆针——比对子句集合。A207 口径同步：上限必须
    # 活着穿过 build 元数据这一站，「无上限回潮」在这里照样红。
    dist_lines = [ln for ln in text.splitlines()
                  if ln.startswith("Requires-Dist:") and 'extra == "stream"' in ln]
    assert len(dist_lines) == 1, f"stream extra 应恰有一条 Requires-Dist：{dist_lines!r}"
    dist_name, dist_clauses = _requirement(
        dist_lines[0].removeprefix("Requires-Dist:").split(";", 1)[0].strip()
    )
    py_name, py_clauses = _stream_requirement_from_pyproject()
    assert dist_name.lower().replace("_", "-") == py_name.lower().replace("_", "-") == "websocket-client"
    assert dist_clauses == py_clauses, (
        f"build 元数据与 pyproject 声明漂移：{sorted(dist_clauses)} != {sorted(py_clauses)}"
    )
    assert any(op in ("<", "<=") for op, _ in dist_clauses), (
        f"A207：上限在 build 元数据里丢了：{dist_lines[0]!r}"
    )
    readme_title = (SDK_DIR / "README.md").read_text(encoding="utf-8").splitlines()[0]
    assert readme_title in text, "readme 作为 [project] 元数据必须写进 METADATA 正文"
