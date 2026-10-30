"""A211（medium）— alembic/env.py 把含 `%` 的 DATABASE_URL 送进 ConfigParser 插值。

台账: docs/ocr-review-2026-10-18/ocr-verified-findings.md §A211
落点: alembic/env.py:35 `config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)`

alembic 的 Config 用带 BasicInterpolation 的 ConfigParser，`get_main_option` 取回时会做一次
`%` 插值。密码里有一个字面 `%`（随机强密码很常见）就要么 `InterpolationSyntaxError`，要么被
当插值语法吃掉 ⇒ `alembic upgrade head` 全红，而 CI 的干净密码测不到。

本测试不复制 env.py 的逻辑：它用 ast 取 env.py 里**那一行** `set_main_option("sqlalchemy.url", …)`
的实参表达式，在真实的 `alembic.config.Config` 上执行，再断言取回值逐字节等于原 URL。
env.py 改回去（丢掉 `%%` 转义或换成别的写法而破坏往返）都会在这里变红。
"""

from __future__ import annotations

import ast
from types import SimpleNamespace
from pathlib import Path

import pytest
from alembic.config import Config

ENV_PY = Path(__file__).resolve().parents[1] / "alembic" / "env.py"

URLS = [
    "postgresql+asyncpg://cds:p%40ss@127.0.0.1:5432/cds",
    "postgresql+asyncpg://cds:p%ss%25word@127.0.0.1:5432/cds",
    "postgresql+asyncpg://cds:100%25@10.0.0.5:5432/cds?sslmode=require",
    "sqlite+aiosqlite:///./ci_a211%31.db",
]


def _set_url_statement() -> ast.Expr:
    tree = ast.parse(ENV_PY.read_text(encoding="utf-8"), filename=str(ENV_PY))
    hits = [
        node
        for node in tree.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "set_main_option"
        and node.value.args
        and isinstance(node.value.args[0], ast.Constant)
        and node.value.args[0].value == "sqlalchemy.url"
    ]
    assert len(hits) == 1, (
        f"alembic/env.py 应有且仅有一处 set_main_option('sqlalchemy.url', …)，实际 {len(hits)} 处"
        "——若改为不经 main option 传 url，请同步改写本用例"
    )
    return hits[0]


@pytest.mark.parametrize("url", URLS)
def test_database_url_survives_alembic_config_roundtrip(url: str) -> None:
    cfg = Config()
    namespace = {"config": cfg, "settings": SimpleNamespace(DATABASE_URL=url)}
    module = ast.Module(body=[_set_url_statement()], type_ignores=[])
    exec(compile(module, "<alembic/env.py set_main_option>", "exec"), namespace)

    try:
        got = cfg.get_main_option("sqlalchemy.url")
    except Exception as exc:  # noqa: BLE001 - 红读数要落在断言行上
        pytest.fail(
            f"A211: 含 % 的 DATABASE_URL 在 alembic Config 往返中炸了 "
            f"{type(exc).__name__}: {exc}"
        )

    assert got == url, f"A211: 取回的 url 被插值改写了\n got={got!r}\nwant={url!r}"
