"""A7 (ocr CR 2026-10-18): 轮找回读未加锁 —— rotate/revoke/PATCH 必须行锁读取。

check-then-act 竞态：修复前三个状态变更端点都用 `db.get(ApiKey, id)` 裸读，
并发下（rotate vs revoke vs PATCH）已撤销的密钥可被写回 active，落进
「active 但 revoked_at 已置」的不一致终态。修复 = SELECT ... FOR UPDATE
（PostgreSQL 行锁互斥；SQLite 方言省略该子句，语义不变）。

断言方式：用 PostgreSQL 方言编译会话上实际执行过的语句，断言
api_keys 的读取带了 FOR UPDATE —— 这是本修复的机器可验不变量。
"""
import uuid

import pytest
from sqlalchemy.dialects import postgresql

from app.core import scopes as ss12_scopes
from app.models.user import User
from app.services.auth_service import create_access_token, hash_password

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clean_ss12_state():
    ss12_scopes.reset_auth_counters()
    ss12_scopes.reset_rate_limits()
    yield
    ss12_scopes.reset_auth_counters()
    ss12_scopes.reset_rate_limits()


@pytest.fixture
def captured_statements(db_session, monkeypatch):
    """记录会话上执行过的语句（按 PG 方言编译后的 SQL 文本）。"""
    captured: list[str] = []
    real_execute = db_session.execute

    async def spy_execute(statement, *args, **kwargs):
        try:
            sql = str(statement.compile(dialect=postgresql.dialect()))
        except Exception:
            sql = str(statement)
        captured.append(sql)
        return await real_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", spy_execute)
    return captured


async def _create_key(client, admin_headers, name="a7-key") -> dict:
    resp = await client.post(
        "/api/v1/api-keys", json={"name": name, "preset": "full"}, headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _locked_reads(statements) -> list[str]:
    hits = [s for s in statements if "FOR UPDATE" in s.upper()]
    return [s for s in hits if "api_keys" in s]


class TestRowLockOnStateMutations:
    async def test_rotate_locks_the_key_row(self, client, db_session, admin_headers, captured_statements):
        key = await _create_key(client, admin_headers, name="a7-rotate")
        resp = await client.post(f"/api/v1/api-keys/{key['id']}/rotate", headers=admin_headers)
        assert resp.status_code == 201, resp.text
        assert _locked_reads(captured_statements), (
            "rotate 必须以 SELECT ... FOR UPDATE 读取被轮换的行（A7 竞态锁）"
        )

    async def test_revoke_locks_the_key_row(self, client, db_session, admin_headers, captured_statements):
        key = await _create_key(client, admin_headers, name="a7-revoke")
        resp = await client.delete(
            f"/api/v1/api-keys/{key['id']}",
            params={"reason": "a7 rotation drill"},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "revoked"
        assert _locked_reads(captured_statements), (
            "revoke 必须以 SELECT ... FOR UPDATE 读取被撤销的行（A7 竞态锁）"
        )

    async def test_patch_locks_the_key_row(self, client, db_session, admin_headers, captured_statements):
        key = await _create_key(client, admin_headers, name="a7-patch")
        resp = await client.patch(
            f"/api/v1/api-keys/{key['id']}",
            json={"status": "disabled"},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "disabled"
        assert _locked_reads(captured_statements), (
            "PATCH 状态变更必须以 SELECT ... FOR UPDATE 读取（A7 竞态锁）"
        )

    async def test_legitimate_lifecycle_still_works(self, client, db_session, admin_headers):
        """收紧不得误伤合法流转：create → disable → enable → rotate → revoke。"""
        key = await _create_key(client, admin_headers, name="a7-lifecycle")

        disabled = await client.patch(
            f"/api/v1/api-keys/{key['id']}", json={"status": "disabled"}, headers=admin_headers,
        )
        assert disabled.status_code == 200 and disabled.json()["status"] == "disabled"

        active = await client.patch(
            f"/api/v1/api-keys/{key['id']}", json={"status": "active"}, headers=admin_headers,
        )
        assert active.status_code == 200 and active.json()["status"] == "active"

        rotated = await client.post(f"/api/v1/api-keys/{key['id']}/rotate", headers=admin_headers)
        assert rotated.status_code == 201, rotated.text

        revoked = await client.delete(
            f"/api/v1/api-keys/{rotated.json()['id']}",
            params={"reason": "a7 lifecycle end"},
            headers=admin_headers,
        )
        assert revoked.status_code == 200 and revoked.json()["status"] == "revoked"

    async def test_revoked_stays_terminal_under_locking(self, client, db_session, admin_headers):
        """revoked 终态守卫在加锁版本里仍成立：撤销后 PATCH 回 active 被拒。"""
        key = await _create_key(client, admin_headers, name="a7-terminal")
        revoked = await client.delete(
            f"/api/v1/api-keys/{key['id']}", params={"reason": "a7 terminal"}, headers=admin_headers,
        )
        assert revoked.status_code == 200

        back = await client.patch(
            f"/api/v1/api-keys/{key['id']}", json={"status": "active"}, headers=admin_headers,
        )
        assert back.status_code == 400
        assert back.json()["code"] == "BAD_REQUEST"
        assert "revoked" in back.json()["detail"]["reason"]
