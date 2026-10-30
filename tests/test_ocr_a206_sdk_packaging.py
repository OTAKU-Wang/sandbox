"""A206 — ``readme = "README.md"`` 写在 ``[project.optional-dependencies]`` 里
⇒ setuptools 配置校验直接报非法，SDK 打包（含 CI 的 ``pip install -e ./sdk``）失败。

PEP 621 规定 extras 的值必须是字符串数组；``readme`` 落进 extras 表后
``[project]`` 段就没有 readme 了。探针口径与账本一致：
``setuptools.config.pyprojecttoml.read_configuration`` 在 HEAD 版本上抛
``ValueError: ... `project.optional-dependencies.readme` must be array``。
"""
import sys
import tomllib
from pathlib import Path

SDK_DIR = Path(__file__).resolve().parents[1] / "sdk"
PYPROJECT = SDK_DIR / "pyproject.toml"

sys.path.insert(0, str(SDK_DIR))


def _pyproject():
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)


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

    assert project["optional-dependencies"] == {"stream": ["websocket-client>=1.6"]}
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
    assert 'Requires-Dist: websocket-client>=1.6; extra == "stream"' in text
    readme_title = (SDK_DIR / "README.md").read_text(encoding="utf-8").splitlines()[0]
    assert readme_title in text, "readme 作为 [project] 元数据必须写进 METADATA 正文"
