"""A217（OCR-10-18 队列）— ``ALEMBIC_COMPARE_TYPES`` 显式布尔解析，未识别值 fail-closed。

旧解析 ``os.environ.get("ALEMBIC_COMPARE_TYPES", "1") == "1"`` 只认字面 ``"1"``：
写 ``true`` / ``yes`` / ``on`` 的人本意是**开**类型对比，实际被**静默关掉**——
一个拼错的开关把门禁降级成无声的假绿。修后：真值 ``1/true/yes/on``、假值
``0/false/no/off``（大小写不敏感、去空白），未置默认开（语义不变），**未识别
值直接抛错**（fail-closed：宁可不跑，不替用户猜方向）。读点在 ``alembic/env.py``
（``scripts/verify_schema_parity.py`` 经 subprocess 继承 env，同一个解析口）。

子进程口径与 ``tests/test_migrations.py`` 一致：``sys.executable -m alembic``，
cwd=REPO_ROOT，独立 SQLite 文件，剥代理变量不碰本机网络。用 ``alembic current``
做探针——它必经 env.py 的 import（解析在模块级执行），但不会真跑迁移链。
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _alembic_current(db: Path, flag: str | None) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "CDS_DATABASE_URL": f"sqlite+aiosqlite:///{db}",
    }
    for var in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        env.pop(var, None)
    env["NO_PROXY"] = "*"
    if flag is None:
        env.pop("ALEMBIC_COMPARE_TYPES", None)
    else:
        env["ALEMBIC_COMPARE_TYPES"] = flag
    return subprocess.run(
        [sys.executable, "-m", "alembic", "current"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=180,
    )


@pytest.mark.parametrize("flag", ["1", "0", "true", "TRUE", "yes", "on", "false", "no", "off", None])
def test_recognized_booleans_are_accepted(tmp_path, flag):
    """显式真/假词全部通过解析（含曾静默翻错的 true/yes/on）。"""
    result = _alembic_current(tmp_path / "db.sqlite", flag)
    assert result.returncode == 0, result.stderr[-800:]


@pytest.mark.parametrize("flag", ["ture", "maybe", "2", "tru e", "-1"])
def test_unrecognized_values_fail_loud_not_silent(tmp_path, flag):
    """未识别值 = 红 + 指名字段；绝不许"悄悄按某一侧"跑下去。"""
    result = _alembic_current(tmp_path / "db.sqlite", flag)
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "ALEMBIC_COMPARE_TYPES" in combined
    assert "A217" in combined


def test_env_no_longer_uses_literal_one_compare(tmp_path):
    """源锁：`== "1"` 解析式不许回来（回来即静默关对比的缺陷复活）。"""
    src = (REPO_ROOT / "alembic" / "env.py").read_text(encoding="utf-8")
    assert 'os.environ.get("ALEMBIC_COMPARE_TYPES", "1") == "1"' not in src
    assert "_parse_compare_types" in src


def test_parse_unit_matrix():
    """纯函数单元面：不起 alembic，逐词钉解析方向（大小写/空白容错）。"""
    import importlib.util

    src = (REPO_ROOT / "alembic" / "env.py").read_text(encoding="utf-8")
    start = src.index("def _parse_compare_types")
    end = src.index("COMPARE_TYPES = _parse_compare_types", start)
    ns: dict = {}
    exec(compile(src[src.index("_COMPARE_TRUTHY"):end], "env_parse_extract", "exec"), ns)
    parse = ns["_parse_compare_types"]
    for truthy in ("1", "true", "True", " TRUE ", "yes", "YES", "on", "On"):
        assert parse(truthy) is True, truthy
    for falsy in ("0", "false", "FALSE", " no", "off ", "Off"):
        assert parse(falsy) is False, falsy
    assert parse(None) is True   # 未置 = 默认开（历史语义）
    assert parse("") is True     # 空串 = 视同未置
    assert parse(" 1 ") is True  # 显式值容错两侧空白（fail-closed 不等于挑刺）
    for bad in ("ture", "2", "y", "-1", "truthy"):
        with pytest.raises(ValueError):
            parse(bad)
