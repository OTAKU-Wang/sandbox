"""OCR 复核轮：A148 / A79 / A8 的回归测试（先红后绿）。

三条都是回读代码时逐字确认过的现场，不依赖任何外部硬件：

- A148 `app/services/merkle_service.py:_sm3_fallback_allowed` —— `except Exception:
  return True` 把 PZ-04/MR-A14 的诚实门翻成 fail-open：配置读不出来时，SHA-256
  冒充 SM3 反而被放行。
- A79 `app/schemas/user.py` —— 用户名正则用 `$`，Python 的 `$` 允许尾随换行，
  `"admin\n"` 可注册，与 `admin` 在 UI/审计里视觉不可区分。
- A8 `app/api/data_products.py` —— `body.row_count or resource.row_count if
  resource else None` 因运算符优先级实际是 `(a or b) if resource else None`，
  未关联资源时调用方显式传的 `row_count` 被丢掉。
"""
import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from app.schemas.user import UserCreate
from app.services import merkle_service


def test_a148_sm3_fallback_gate_fails_closed_when_settings_unreadable(monkeypatch):
    """配置读不出来时必须**不放行** SHA-256 冒充 SM3。"""

    def boom():
        raise RuntimeError("settings unavailable")

    import app.core.config as config

    monkeypatch.setattr(config, "get_settings", boom)
    assert merkle_service._sm3_fallback_allowed() is False


def test_a148_sm3_fallback_gate_follows_settings(monkeypatch):
    class _S:
        SM3_FALLBACK_ALLOWED = False

    import app.core.config as config

    monkeypatch.setattr(config, "get_settings", lambda: _S())
    assert merkle_service._sm3_fallback_allowed() is False

    _S.SM3_FALLBACK_ALLOWED = True
    assert merkle_service._sm3_fallback_allowed() is True


def test_a079_username_rejects_trailing_newline():
    with pytest.raises(ValidationError, match="alphanumeric"):
        UserCreate(username="admin\n", email="a@b.example", password="Sup3rSecret!")


def test_a079_username_accepts_plain_value():
    user = UserCreate(username="alice_01", email="a@b.example", password="Sup3rSecret!")
    assert user.username == "alice_01"


@pytest.mark.asyncio
async def test_a008_explicit_row_count_survives_without_resource(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        "/api/v1/data-products",
        json={"name": "RowCount Explicit", "row_count": 123},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["row_count"] == 123
