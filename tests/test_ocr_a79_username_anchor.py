r"""OCR A79 — 用户名正则用 `$`，尾随换行可通过（medium）。

台账（docs/ocr-review-2026-10-18/ocr-verified-findings.md ### A79）：`app/schemas/user.py`
的用户名校验原本写作 `re.match(r"^[a-zA-Z0-9_]+$", v)`。Python 里非 MULTILINE 的 `$`
除串尾外还额外匹配"最后一个换行符之前"，所以 `"admin\n"` 通过校验落库 —— 与 `admin`
在 UI / 审计 / 报表里视觉不可区分（登录是精确匹配，拿不到 admin 账号，但运维误认与
日志换行注入是真实后果）。修法口径：绝对末尾锚（`\Z` 或 `re.fullmatch`）。

现状：HEAD 已带 `\Z`（`git show 5aa84a8:app/schemas/user.py` 起），schema 级单点钉在
`tests/test_ocr_a148_a079_a008.py::test_a079_username_rejects_trailing_newline`。
本文件补它没覆盖的两层：
1) 全边界集：尾随 `\n`、串内 `\n`、空白、控制字符、非 ASCII 同形字符一律拒；
2) 路由级：`POST /api/v1/auth/register` 对 `"admin\n"` 返回 422 且**不留下任何用户行**
   —— 钉住"锚点挂在注册路由的校验链上"，而不只是存在于一个可被绕过的 schema 里。

断言刻意只针对 `loc` 里的字段名与状态码，不钉错误文案（纯文案不做契约测试）。
"""
import uuid

import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from app.schemas.user import UserCreate

# `admin\n` 是台账原案（旧 `$` 锚放行它）；其余行在旧写法下大多也已被拒，列在这里
# 是为了钉住"空白/控制字符/非 ASCII 同形字符都不可入用户名"这条边界，不是复现旧缺陷。
REJECTED_USERNAMES = [
    "admin\n",       # 台账原案：`$` 放行尾随换行
    "admin\r\n",
    "admin\n\n",
    "ad\nmin",
    " admin",
    "admin ",
    "adm\tin",
    "admin\x00",
    "admin\v",
    "аdmin",         # 西里尔 а：与 latin a 同形
    "ab",            # 少于 3 字符
]

ACCEPTED_USERNAMES = ["abc", "alice_01", "A_9_b"]


def _user_create(username: str) -> UserCreate:
    unique = uuid.uuid4().hex[:8]
    return UserCreate(
        username=username,
        email=f"a79_{unique}@example.com",
        password="Sup3rSecret!",
    )


@pytest.mark.parametrize("username", REJECTED_USERNAMES)
def test_a79_username_rejects_anchor_bypass(username: str) -> None:
    with pytest.raises(ValidationError):
        _user_create(username)


@pytest.mark.parametrize("username", ACCEPTED_USERNAMES)
def test_a79_username_accepts_legal_values(username: str) -> None:
    assert _user_create(username).username == username


def test_a79_schema_source_has_absolute_end_anchor() -> None:
    """结构钉：校验必须用绝对末尾锚，不得退回 `$`。

    `re.fullmatch(r"^[a-zA-Z0-9_]+$")` 同样安全，但 `re.match(..., "$")` 不是；
    这里只接受源码里出现 `\\Z` 或 `fullmatch`，拒掉 `$` 收尾的写法。
    """
    import inspect
    import re

    from app.schemas import user as user_schema

    src = inspect.getsource(user_schema.UserCreate.username_alphanumeric.__func__)
    pattern = re.search(r"""r"([^"]+)"|r'([^']+)'""", src)
    assert pattern is not None, f"找不到用户名校验正则：{src!r}"
    regex = pattern.group(1) or pattern.group(2)
    assert (r"\Z" in regex or "fullmatch" in src), f"用户端正则缺绝对末尾锚: {regex!r}"
    # 自身反证：把锚换成 `$` 就必须能放行尾随换行（否则本结构钉是空转的）
    assert re.match(regex.replace(r"\Z", "$") + "", "admin\n") is not None


@pytest.mark.asyncio
async def test_a79_register_route_rejects_trailing_newline(
    client: AsyncClient, db_session
) -> None:
    """路由级：`"admin\\n"` 到不了 DB —— 422 且没有任何用户行落地。"""
    from sqlalchemy import func, select

    from app.models.user import User

    newline_user = "a79_admin\n"
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "username": newline_user,
            "email": "a79_newline@example.com",
            "password": "Sup3rSecret!",
        },
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert any(err["loc"][-1] == "username" for err in detail), detail

    rows = await db_session.scalar(
        select(func.count()).select_from(User).where(User.username == newline_user)
    )
    assert rows == 0

    # 对照：同一形状的合法用户名仍能注册（证明 422 来自锚点，不是别的载荷问题）
    unique = uuid.uuid4().hex[:8]
    ok = await client.post(
        "/api/v1/auth/register",
        json={
            "username": f"a79_ok_{unique}",
            "email": f"a79_ok_{unique}@example.com",
            "password": "Sup3rSecret!",
        },
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["user"]["username"] == f"a79_ok_{unique}"
