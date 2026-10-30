"""A78 — SDK 打包元数据错位（high）：版本双来源半边。

Ledger (docs/ocr-review-2026-10-18/ocr-verified-findings.md ### A78) records two
defects in the SDK packaging: the misplaced `readme` key (covered by
`test_ocr_a154_pyproject_metadata.py` / `test_ocr_a206_sdk_packaging.py`) and
`sdk/cds_sdk/__init__.py:22` hardcoding `__version__ = "0.2.0"` while
`sdk/pyproject.toml` says `0.3.0` — pip side 0.3.0, code side 0.2.0. Ledger fix:
"版本改单一来源" (single source for the version).

Here `[project].version` in `sdk/pyproject.toml` is the single source. These tests
load the package straight out of the tree under test (the repo suite does the same
via `sys.path`; no `cds-sdk` distribution is installed here) and pin byte-level
equality plus the fallbacks that keep `import cds_sdk` from raising ImportError on
a bare checkout.
"""
import importlib.metadata
import importlib.util
import re
import shutil
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
SDK_DIR = REPO / "sdk"
PKG_DIR = SDK_DIR / "cds_sdk"

UNKNOWN = "0.0.0+unknown"


def _pyproject(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _pyproject_version() -> str:
    return _pyproject(SDK_DIR / "pyproject.toml")["project"]["version"]


def _load_package(pkg_dir: Path, name: str):
    spec = importlib.util.spec_from_file_location(
        name, pkg_dir / "__init__.py", submodule_search_locations=[str(pkg_dir)]
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        for key in [k for k in sys.modules if k == name or k.startswith(f"{name}.")]:
            sys.modules.pop(key, None)


def _copy_package(dest_parent: Path) -> Path:
    dest = dest_parent / "cds_sdk"
    shutil.copytree(PKG_DIR, dest, ignore=shutil.ignore_patterns("__pycache__"))
    return dest


@pytest.fixture(autouse=True)
def _no_dist_metadata(monkeypatch):
    def _raise(_name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(_name)

    monkeypatch.setattr(importlib.metadata, "version", _raise)


def test_version_matches_pyproject_byte_for_byte():
    module = _load_package(PKG_DIR, "cds_sdk_tree_check")
    assert module.__version__ == _pyproject_version()


def test_stale_second_source_value_is_gone():
    module = _load_package(PKG_DIR, "cds_sdk_stale_check")
    assert module.__version__ != "0.2.0"
    assert _pyproject_version() == "0.3.0"


def test_init_declares_no_hardcoded_version_literal():
    source = (PKG_DIR / "__init__.py").read_text(encoding="utf-8")
    assert re.search(r"(?m)^__version__\s*=\s*[\"']", source) is None, (
        "__init__.py 里又出现了字面量 __version__ —— 版本回到了双来源（A78）"
    )


def test_version_comes_from_pyproject_when_dist_metadata_missing():
    module = _load_package(PKG_DIR, "cds_sdk_tree_no_dist_check")
    assert module.__version__ == _pyproject_version()


def test_import_works_without_installed_distribution(tmp_path):
    module = _load_package(_copy_package(tmp_path / "bare"), "cds_sdk_bare_import_check")
    assert module.__version__ == UNKNOWN, (
        "既没有 pyproject 也没有发行元数据时必须披露未知版本，而不是抛 ImportError 或谎报版本"
    )


def test_foreign_pyproject_next_to_the_package_is_not_read(tmp_path, monkeypatch):
    staging = tmp_path / "foreign"
    _copy_package(staging)
    (staging / "pyproject.toml").write_text(
        '[project]\nname = "some-other-project"\nversion = "9.9.9"\n', encoding="utf-8"
    )
    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "1.2.3+from-dist")
    module = _load_package(staging / "cds_sdk", "cds_sdk_foreign_pyproject_check")
    assert module.__version__ == "1.2.3+from-dist"


def test_dist_metadata_is_used_for_an_installed_package(tmp_path, monkeypatch):
    site = tmp_path / "site-packages"
    _copy_package(site)
    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "0.4.2+wheel")
    module = _load_package(site / "cds_sdk", "cds_sdk_installed_check")
    assert module.__version__ == "0.4.2+wheel"
