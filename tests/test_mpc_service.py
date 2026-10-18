"""MPC key sharing protocol tests (DB-persisted, spec N3)."""
import os

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_split_and_reconstruct(db_session):
    """Unit test: split secret and reconstruct from threshold shares."""
    from app.services.mpc_service import MPCService
    svc = MPCService()
    secret = os.urandom(32)
    key = await svc.split_key(db_session, secret, threshold=3, total_shares=5)
    assert key.threshold == 3
    assert key.total_shares == 5
    assert len(key.shares) == 5

    # Reconstruct from first 3 shares
    share_ids = [s.share_id for s in key.shares[:3]]
    reconstructed = await svc.reconstruct_key(db_session, key.key_id, share_ids)
    assert reconstructed == secret


@pytest.mark.asyncio
async def test_reconstruct_different_combinations(db_session):
    """Unit test: any K shares can reconstruct."""
    from app.services.mpc_service import MPCService
    svc = MPCService()
    secret = os.urandom(32)
    key = await svc.split_key(db_session, secret, threshold=2, total_shares=4)

    # Try different pairs
    for i in range(4):
        for j in range(i + 1, 4):
            share_ids = [key.shares[i].share_id, key.shares[j].share_id]
            result = await svc.reconstruct_key(db_session, key.key_id, share_ids)
            assert result == secret, f"Failed for pair ({i}, {j})"


@pytest.mark.asyncio
async def test_insufficient_shares(db_session):
    """Unit test: below threshold fails."""
    from app.services.mpc_service import MPCService
    svc = MPCService()
    secret = os.urandom(32)
    key = await svc.split_key(db_session, secret, threshold=3, total_shares=5)

    with pytest.raises(ValueError, match="Need at least 3"):
        await svc.reconstruct_key(db_session, key.key_id, [key.shares[0].share_id])


@pytest.mark.asyncio
async def test_invalid_share(db_session):
    """Unit test: invalid share ID fails."""
    from app.services.mpc_service import MPCService
    svc = MPCService()
    secret = os.urandom(32)
    key = await svc.split_key(db_session, secret, threshold=2, total_shares=3)

    with pytest.raises(ValueError, match="Need at least 2"):
        await svc.reconstruct_key(db_session, key.key_id, [key.shares[0].share_id, "nonexistent"])


@pytest.mark.asyncio
async def test_threshold_must_be_at_least_2(db_session):
    """Unit test: threshold < 2 raises error."""
    from app.services.mpc_service import MPCService
    svc = MPCService()
    with pytest.raises(ValueError, match="at least 2"):
        await svc.split_key(db_session, os.urandom(32), threshold=1, total_shares=3)


@pytest.mark.asyncio
async def test_total_shares_must_exceed_threshold(db_session):
    """Unit test: total < threshold raises error."""
    from app.services.mpc_service import MPCService
    svc = MPCService()
    with pytest.raises(ValueError, match="must be >= threshold"):
        await svc.split_key(db_session, os.urandom(32), threshold=5, total_shares=3)


@pytest.mark.asyncio
async def test_with_holders(db_session):
    """Unit test: shares have holder assignments."""
    from app.services.mpc_service import MPCService
    svc = MPCService()
    holders = ["alice", "bob", "charlie"]
    key = await svc.split_key(db_session, os.urandom(32), threshold=2, total_shares=3, holders=holders)
    for i, share in enumerate(key.shares):
        assert share.holder == holders[i]


@pytest.mark.asyncio
async def test_rotate_key(db_session):
    """Unit test: key rotation generates new key."""
    from app.services.mpc_service import MPCService
    svc = MPCService()
    secret = os.urandom(32)
    key = await svc.split_key(db_session, secret, threshold=2, total_shares=3)
    old_shares = [s.share_value for s in key.shares]

    new_key = await svc.rotate_key(db_session, key.key_id)
    # Key ID changes
    assert new_key.key_id != key.key_id
    # Shares should be different
    new_shares = [s.share_value for s in new_key.shares]
    assert old_shares != new_shares


@pytest.mark.asyncio
async def test_verify_shares(db_session):
    """Unit test: verify shares without reconstruction."""
    from app.services.mpc_service import MPCService
    svc = MPCService()
    secret = os.urandom(32)
    key = await svc.split_key(db_session, secret, threshold=2, total_shares=3)

    share_ids = [s.share_id for s in key.shares[:2]]
    assert await svc.verify_shares(db_session, key.key_id, share_ids) is True
    assert await svc.verify_shares(db_session, key.key_id, [key.shares[0].share_id]) is False
    assert await svc.verify_shares(db_session, "nonexistent", share_ids) is False


@pytest.mark.asyncio
async def test_list_keys(db_session):
    """Unit test: list keys."""
    from app.services.mpc_service import MPCService
    svc = MPCService()
    await svc.split_key(db_session, os.urandom(32), threshold=2, total_shares=3)
    await svc.split_key(db_session, os.urandom(32), threshold=3, total_shares=5)
    assert len(await svc.list_keys(db_session)) >= 2


# API tests
@pytest.mark.asyncio
async def test_api_split_key(client: AsyncClient, operator_headers: dict):
    """API test: split a key."""
    resp = await client.post("/api/v1/mpc/split", json={
        "secret_hex": "aabbccdd" * 8,
        "threshold": 2,
        "total_shares": 3,
    }, headers=operator_headers)
    assert resp.status_code == 200
    shares = resp.json()
    assert len(shares) == 3
    assert shares[0]["threshold"] == 2


@pytest.mark.asyncio
async def test_api_split_and_reconstruct(client: AsyncClient, operator_headers: dict):
    """API test: split then reconstruct."""
    secret_hex = "1122334455667788" * 4
    split_resp = await client.post("/api/v1/mpc/split", json={
        "secret_hex": secret_hex,
        "threshold": 2,
        "total_shares": 3,
    }, headers=operator_headers)
    shares = split_resp.json()
    key_id = shares[0]["key_id"]

    recon_resp = await client.post("/api/v1/mpc/reconstruct", json={
        "key_id": key_id,
        "share_ids": [shares[0]["share_id"], shares[1]["share_id"]],
    }, headers=operator_headers)
    assert recon_resp.status_code == 200
    assert recon_resp.json()["secret_hex"] == secret_hex


@pytest.mark.asyncio
async def test_api_list_keys(client: AsyncClient, operator_headers: dict):
    """API test: list keys."""
    resp = await client.get("/api/v1/mpc/keys", headers=operator_headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_api_rotate_key(client: AsyncClient, make_user):
    """API test: rotate key (admin only)."""
    admin_headers, _ = await make_user("admin", "mpc_rotate")
    # Create a key first
    split_resp = await client.post("/api/v1/mpc/split", json={
        "secret_hex": "aabb" * 16,
        "threshold": 2,
        "total_shares": 3,
    }, headers=admin_headers)
    key_id = split_resp.json()[0]["key_id"]

    resp = await client.post(f"/api/v1/mpc/keys/{key_id}/rotate", headers=admin_headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_api_destroy_key(client: AsyncClient, make_user):
    """API test: destroy key (admin only) then reconstruct fails."""
    admin_headers, _ = await make_user("admin", "mpc_destroy")
    split_resp = await client.post("/api/v1/mpc/split", json={
        "secret_hex": "aabb" * 16,
        "threshold": 2,
        "total_shares": 3,
    }, headers=admin_headers)
    shares = split_resp.json()
    key_id = shares[0]["key_id"]

    resp = await client.post(f"/api/v1/mpc/keys/{key_id}/destroy", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["destroyed"] is True

    recon_resp = await client.post("/api/v1/mpc/reconstruct", json={
        "key_id": key_id,
        "share_ids": [shares[0]["share_id"], shares[1]["share_id"]],
    }, headers=admin_headers)
    assert recon_resp.status_code == 400


@pytest.mark.asyncio
async def test_api_buyer_cannot_split(client: AsyncClient, auth_headers: dict):
    """API test: buyer cannot split keys."""
    resp = await client.post("/api/v1/mpc/split", json={
        "secret_hex": "aabb" * 16,
        "threshold": 2,
        "total_shares": 3,
    }, headers=auth_headers)
    assert resp.status_code == 403


# ─── 读侧授权（CRITICAL）：share_value 明文 = 可重构的主密钥 ───────────────────
# GET /keys/{id}/shares 与 GET /keys 曾经只靠 get_current_user：任何已认证
# 用户都能取走全部密钥清单与每个密钥的全部分片，凑齐 threshold 份即可离线
# Lagrange 重构。门禁与写侧对齐（见 app/api/mpc.py rotate/destroy）。

@pytest.mark.asyncio
async def test_api_list_shares_is_admin_only(client: AsyncClient, make_user):
    """GET /keys/{key_id}/shares 返回 share_value 明文 —— 非 ADMIN 一律 403。

    回归：修复前本端点只要求已认证，data_provider/buyer/operator 均 200 并拿到
    全部分片，足以重构密钥。现在收口到本 router 最严的 ADMIN 组。
    """
    admin_headers, _ = await make_user("admin", "mpc_sh_admin")
    split = await client.post("/api/v1/mpc/split", json={
        "secret_hex": "aabb" * 16,
        "threshold": 2,
        "total_shares": 3,
    }, headers=admin_headers)
    assert split.status_code == 200
    key_id = split.json()[0]["key_id"]

    # 注意：下列角色均不在 ADMIN 允许集内（包括 operator）
    for role, prefix in (
        ("data_provider", "mpc_sh_dp"),
        ("buyer", "mpc_sh_buyer"),
        ("operator", "mpc_sh_op"),
        ("regulator", "mpc_sh_reg"),
    ):
        headers, _uid = await make_user(role, prefix)
        resp = await client.get(f"/api/v1/mpc/keys/{key_id}/shares", headers=headers)
        assert resp.status_code == 403, f"{role} must not read shares: {resp.status_code} {resp.text}"
        assert resp.json().get("code") == "HTTP_403"  # W2 错误体
        assert "share_value" not in resp.text

    ok = await client.get(f"/api/v1/mpc/keys/{key_id}/shares", headers=admin_headers)
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert len(body) == 3
    assert {s["index"] for s in body} == {1, 2, 3}


@pytest.mark.asyncio
async def test_api_key_inventory_denies_non_ops_roles(client: AsyncClient, make_user):
    """GET /keys 与 GET /keys/{id} 收口到 ADMIN/OPERATOR（同 split/reconstruct）。

    mpc_keys 没有归属列（app/models/mpc_key.py），无法按属主过滤，角色是唯一
    边界；所以清单对 data_provider/buyer/regulator 403，对 operator 仍 200
    且看到的是全量台账（角色过滤，而不是属主过滤）。
    """
    admin_headers, _ = await make_user("admin", "mpc_lk_admin")
    split = await client.post("/api/v1/mpc/split", json={
        "secret_hex": "ccdd" * 16,
        "threshold": 2,
        "total_shares": 3,
    }, headers=admin_headers)
    assert split.status_code == 200
    key_id = split.json()[0]["key_id"]

    for role, prefix in (
        ("data_provider", "mpc_lk_dp"),
        ("buyer", "mpc_lk_buyer"),
        ("regulator", "mpc_lk_reg"),
    ):
        headers, _uid = await make_user(role, prefix)
        listed = await client.get("/api/v1/mpc/keys", headers=headers)
        assert listed.status_code == 403, f"{role} must not list keys: {listed.text}"
        assert listed.json().get("code") == "HTTP_403"
        # 按 id 取详情不得成为清单门禁的绕路
        detail = await client.get(f"/api/v1/mpc/keys/{key_id}", headers=headers)
        assert detail.status_code == 403, f"{role} must not read key detail: {detail.text}"

    op_headers, _uid = await make_user("operator", "mpc_lk_op")
    resp = await client.get("/api/v1/mpc/keys", headers=op_headers)
    assert resp.status_code == 200, resp.text
    assert key_id in [k["key_id"] for k in resp.json()]

    one = await client.get(f"/api/v1/mpc/keys/{key_id}", headers=op_headers)
    assert one.status_code == 200, one.text
    assert one.json()["key_id"] == key_id


@pytest.mark.asyncio
async def test_api_list_shares_records_audit(client: AsyncClient, make_user, db_session):
    """读取分片明文必须留痕 —— 审计台账里要能查到“谁在什么时候读了哪个密钥的分片”。

    回归：本端点返回 share_value 明文（凑齐 threshold 份即可重构密钥），但以前
    只有 split/reconstruct/rotate/destroy 四个写侧动作落审计，读侧一声不响。
    """
    from sqlalchemy import select

    from app.models.audit_log import AuditLog

    admin_headers, admin_id = await make_user("admin", "mpc_au_admin")
    split = await client.post("/api/v1/mpc/split", json={
        "secret_hex": "cc" * 16,
        "threshold": 2,
        "total_shares": 3,
    }, headers=admin_headers)
    assert split.status_code == 200
    key_id = split.json()[0]["key_id"]

    denied, _uid = await make_user("data_provider", "mpc_au_dp")
    bad = await client.get(f"/api/v1/mpc/keys/{key_id}/shares", headers=denied)
    assert bad.status_code == 403

    ok = await client.get(f"/api/v1/mpc/keys/{key_id}/shares", headers=admin_headers)
    assert ok.status_code == 200, ok.text

    rows = (await db_session.execute(
        select(AuditLog).where(AuditLog.action == "mpc.key.read_shares")
    )).scalars().all()
    matched = [r for r in rows if r.resource_id == key_id]
    assert len(matched) == 1, f"expected exactly one read audit row, got {len(matched)}"
    assert str(matched[0].user_id) == str(admin_id)
    assert (matched[0].detail or {}).get("share_count") == 3

    # 被拒的读取不得伪装成一次成功读取：403 不应多写一条 read_shares
    assert len([r for r in rows if r.resource_id == key_id and str(r.user_id) != str(admin_id)]) == 0
