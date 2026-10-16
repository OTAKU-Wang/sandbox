"""前端调用 ↔ 后端路由 静态对账（SS-15 P1 契约守卫）。

背景：产品化评审里发现"发明的端点"是真实失效模式（原型/PRD 引用的路径后端并不存在），
而这类漂移在运行时只表现为页面 404，CI 完全看不见。本测试在 CI 阶段把
`cds-frontend/src/services/*.ts` 里的每一条字面量请求路径与 `app.main:app` 的真实路由表对齐。

规则（与 `services/api.ts` 的实际拼法一致）：
- 共享实例 `api.<verb>('/x')` → 实际请求 `/api/v1/x`（baseURL = VITE_API_BASE_URL || '/api/v1'）；
- 绝对路径（`/api/...`、`/open/...`、`/health`）按原样匹配；
- 查询串不参与路由匹配；
- TS 模板串插值 `${...}` 视为一个动态段，只要存在同形状的路由即通过。

已知例外（登记在此处，改名即失配 → 测试变红，正是本测试要防的事）：
- `/dev-sandbox/templates/<name>` 后端是 4 个**固定名**路由而非路径参数；
- 只匹配字面量路径，`api.get(urlVar)` 这类变量拼接不在覆盖范围内。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / "cds-frontend" / "src" / "services"
TEMPLATE_PAGE = ROOT / "cds-frontend" / "src" / "pages" / "Sandbox" / "DataProductDev.tsx"

if not SERVICES.is_dir():
    pytest.skip(
        "cds-frontend/src/services 未同步到测试树，跳过前端路由对账",
        allow_module_level=True,
    )

BASE = "/api/v1"
CALL_RE = re.compile(
    r"\b(?:api|axios)\.(get|post|put|patch|delete)\(\s*"
    r"(?:`([^`]*)`|'([^']*)'|\"([^\"]*)\")"
)
# `${import.meta.env.VITE_API_BASE_URL || '/api/v1'}/auth/refresh` 这类写法等价于 baseURL + 余下路径
BASE_PREFIX_RE = re.compile(r"^\$\{[^{}]*VITE_API_BASE_URL[^{}]*\}")
DYN_RE = re.compile(r"\$\{[^{}]*\}")
ABSOLUTE_PREFIXES = ("/api/", "/open/", "/health")

DEV_TEMPLATE_CALL = ("GET", "/api/v1/dev-sandbox/templates/<dyn>")
DEV_TEMPLATE_NAMES = {"pandas", "sql", "json-etl", "image-batch"}


def _walk_routes(node) -> set[tuple[str, str]]:
    rows: set[tuple[str, str]] = set()
    for route in getattr(node, "routes", None) or []:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if path and methods:
            rows |= {(m, path) for m in methods - {"HEAD", "OPTIONS"}}
        for child in (getattr(route, "routes", None), getattr(route, "router", None)):
            if child is not None:
                rows |= _walk_routes(child)
    return rows


def _route_table() -> set[tuple[str, str]]:
    """路由表取法与 FastAPI/Starlette 版本无关：递归遍历 + openapi 文档并集（新版 Starlette 把
    include_router 结果包成惰性 _IncludedRouter，app.routes 不再摊平 APIRoute）。"""
    from app.main import app

    rows = _walk_routes(app)
    for path, ops in (app.openapi().get("paths") or {}).items():
        for method in ops:
            rows.add((method.upper(), path))
    return {(m, re.sub(r"\{[^}]+\}", "<dyn>", p)) for m, p in rows}


def _frontend_calls() -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    for module in sorted(SERVICES.glob("*.ts")):
        text = module.read_text(encoding="utf-8")
        for match in CALL_RE.finditer(text):
            raw = next(g for g in match.groups()[1:] if g is not None)
            path = raw.split("?", 1)[0]
            path = BASE_PREFIX_RE.sub(BASE, path)
            path = DYN_RE.sub("<dyn>", path)
            if not path.startswith(ABSOLUTE_PREFIXES):
                path = BASE + path
            found.append((match.group(1).upper(), path, module.name))
    return found


def _matches(method: str, fe_path: str, routes: set[tuple[str, str]]) -> bool:
    for m, rp in routes:
        if m != method:
            continue
        if rp == fe_path:
            return True
        if "<dyn>" not in fe_path:
            continue
        # 动态段按单段通配匹配（`<dyn>` 不对跨段展开，避免把 `/a/b` 糊到 `/a/{x}/c`）
        pattern = "^" + re.escape(fe_path).replace(re.escape("<dyn>"), "[^/]+") + "$"
        if re.match(pattern, rp):
            return True
    return False


@pytest.fixture(scope="module")
def routes() -> set[tuple[str, str]]:
    return _route_table()


def test_frontend_calls_resolve_to_real_routes(routes):
    calls = _frontend_calls()
    assert len(calls) >= 150, f"仅解析到 {len(calls)} 条前端调用，解析器可能已失效"
    missing = sorted({(m, p, f) for m, p, f in calls if not _matches(m, p, routes)})
    assert not missing, "前端调用了后端不存在的端点：\n" + "\n".join(
        f"  {m} {p}  <- {f}" for m, p, f in missing
    )


def test_no_call_relies_on_trailing_slash_redirect(routes):
    """少了尾斜杠的路径靠 FastAPI 307 兜住：能过，但多一跳且经代理时脆弱，登记为回归。"""
    redirected = sorted(
        {
            (m, p, f)
            for m, p, f in _frontend_calls()
            if (m, p) not in routes and (m, p + "/") in routes
        }
    )
    assert not redirected, "以下调用依赖 307 补尾斜杠，请写全：\n" + "\n".join(
        f"  {m} {p}  <- {f}" for m, p, f in redirected
    )


def test_dev_template_names_exist_as_literal_routes(routes):
    """`dev-sandbox/templates/${name}` 的后端是固定名路由：两侧名字集合必须一致。"""
    calls = _frontend_calls()
    assert (DEV_TEMPLATE_CALL[0], DEV_TEMPLATE_CALL[1], "sandboxApi.ts") in [
        (m, p, f) for m, p, f in calls
    ], "sandboxApi 的模板调用写法已变更，请同步本守卫"

    backend_names = {
        rp.rsplit("/", 1)[-1]
        for m, rp in routes
        if m == "GET" and rp.startswith("/api/v1/dev-sandbox/templates/")
    }
    assert backend_names == DEV_TEMPLATE_NAMES, f"后端模板路由名漂移：{sorted(backend_names)}"

    if TEMPLATE_PAGE.is_file():
        page = TEMPLATE_PAGE.read_text(encoding="utf-8")
        block = re.search(r"const templates = \[(.*?)\n\s*\];", page, re.S)
        assert block, "DataProductDev.tsx 的 templates 常量结构已变更"
        keys = set(re.findall(r"key:\s*'([^']+)'", block.group(1)))
        assert keys, "未解析到模板 key"
        assert keys <= backend_names, f"前端模板名后端不存在：{sorted(keys - backend_names)}"
