"""PZ-10 (spec ss-11): auth enhancement — MFA TOTP, role governance, SM2 cert login.

Covers: enroll→verify→login-with-MFA happy path, AUTH_MFA_REQUIRED on
missing/invalid code, one-shot recovery codes + re-enroll via /mfa/recover,
PATCH role enum validation, the tightened register role whitelist, the
0012_user_mfa migration, and the SM2 cert-login second factor.
"""
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import _totp_code
from app.core.database import Base  # noqa: F401 — user_mfa registered via app.api.auth import
from app.models.user import User

REPO_ROOT = Path(__file__).resolve().parents[1]

PASSWORD = "testpass123"


async def _create_user(db_session: AsyncSession, role: str = "buyer", prefix: str = "pz10") -> User:
    """Create a user directly in the test DB (registration no longer accepts
    non-admin roles — see the whitelist tests below)."""
    from app.services.auth_service import hash_password

    unique = uuid.uuid4().hex[:8]
    username = f"{prefix}_{unique}"
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password(PASSWORD),
        role=role,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


def _headers(user: User) -> dict:
    from app.services.auth_service import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


async def _enroll_and_confirm(client: AsyncClient, headers: dict) -> tuple[str, list[str]]:
    """enroll → verify; returns (totp_secret, recovery_codes)."""
    resp = await client.post("/api/v1/auth/mfa/enroll", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    secret = data["secret"]
    assert data["confirmed"] is False
    assert f"secret={secret}" in data["otpauth_uri"]
    assert data["otpauth_uri"].startswith("otpauth://totp/")

    resp = await client.post("/api/v1/auth/mfa/verify", json={"code": _totp_code(secret)}, headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["confirmed"] is True
    assert len(data["recovery_codes"]) == 8
    return secret, data["recovery_codes"]


# ── MFA happy path + AUTH_MFA_REQUIRED ───────────────────────────────

@pytest.mark.asyncio
async def test_mfa_enroll_verify_login_happy_path(client: AsyncClient, db_session: AsyncSession):
    user = await _create_user(db_session)
    headers = _headers(user)

    secret, _codes = await _enroll_and_confirm(client, headers)

    # Login WITH a valid TOTP code succeeds
    resp = await client.post("/api/v1/auth/login", json={
        "username": user.username, "password": PASSWORD, "mfa_code": _totp_code(secret),
    })
    assert resp.status_code == 200, resp.text
    assert "access_token" in resp.json()
    assert resp.json()["user"]["username"] == user.username


@pytest.mark.asyncio
async def test_login_without_mfa_code_returns_auth_mfa_required(client: AsyncClient, db_session: AsyncSession):
    user = await _create_user(db_session)
    headers = _headers(user)
    await _enroll_and_confirm(client, headers)

    resp = await client.post("/api/v1/auth/login", json={
        "username": user.username, "password": PASSWORD,
    })
    assert resp.status_code == 401
    assert resp.json()["code"] == "AUTH_MFA_REQUIRED"
    assert "request_id" in resp.json()


@pytest.mark.asyncio
async def test_login_with_invalid_mfa_code_returns_auth_mfa_required(client: AsyncClient, db_session: AsyncSession):
    user = await _create_user(db_session)
    headers = _headers(user)
    await _enroll_and_confirm(client, headers)

    resp = await client.post("/api/v1/auth/login", json={
        "username": user.username, "password": PASSWORD, "mfa_code": "000000",
    })
    assert resp.status_code == 401
    assert resp.json()["code"] == "AUTH_MFA_REQUIRED"


@pytest.mark.asyncio
async def test_unconfirmed_enrollment_does_not_block_login(client: AsyncClient, db_session: AsyncSession):
    """A row in flight (enrolled, unverified) must not lock the user out."""
    user = await _create_user(db_session)
    headers = _headers(user)
    resp = await client.post("/api/v1/auth/mfa/enroll", headers=headers)
    assert resp.status_code == 200

    resp = await client.post("/api/v1/auth/login", json={
        "username": user.username, "password": PASSWORD,
    })
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_login_with_recovery_code_is_one_shot(client: AsyncClient, db_session: AsyncSession):
    user = await _create_user(db_session)
    headers = _headers(user)
    _secret, codes = await _enroll_and_confirm(client, headers)

    payload = {"username": user.username, "password": PASSWORD, "mfa_code": codes[0]}
    resp = await client.post("/api/v1/auth/login", json=payload)
    assert resp.status_code == 200, resp.text
    assert "access_token" in resp.json()

    # the same recovery code is already spent
    resp = await client.post("/api/v1/auth/login", json=payload)
    assert resp.status_code == 401
    assert resp.json()["code"] == "AUTH_MFA_REQUIRED"


@pytest.mark.asyncio
async def test_mfa_recover_spends_code_and_reenrolls(client: AsyncClient, db_session: AsyncSession):
    user = await _create_user(db_session)
    headers = _headers(user)
    _secret, codes = await _enroll_and_confirm(client, headers)

    resp = await client.post("/api/v1/auth/mfa/recover", json={"recovery_code": codes[0]}, headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["confirmed"] is False
    assert data["secret"]
    assert f"secret={data['secret']}" in data["otpauth_uri"]
    assert data["remaining_recovery_codes"] == 7

    # the spent code does not work a second time
    resp = await client.post("/api/v1/auth/mfa/recover", json={"recovery_code": codes[0]}, headers=headers)
    assert resp.status_code == 400

    # re-verification with the NEW secret re-arms MFA login enforcement
    resp = await client.post("/api/v1/auth/mfa/verify", json={"code": _totp_code(data["secret"])}, headers=headers)
    assert resp.status_code == 200
    resp = await client.post("/api/v1/auth/login", json={
        "username": user.username, "password": PASSWORD,
    })
    assert resp.status_code == 401
    assert resp.json()["code"] == "AUTH_MFA_REQUIRED"


@pytest.mark.asyncio
async def test_mfa_verify_requires_enrollment(client: AsyncClient, db_session: AsyncSession):
    user = await _create_user(db_session)
    headers = _headers(user)
    resp = await client.post("/api/v1/auth/mfa/verify", json={"code": "123456"}, headers=headers)
    assert resp.status_code == 400


# ── Role governance ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_role_patch_invalid_enum_rejected(client: AsyncClient, db_session: AsyncSession):
    admin = await _create_user(db_session, role="admin", prefix="pz10admin")
    target = await _create_user(db_session, role="buyer", prefix="pz10tgt")
    admin_headers = _headers(admin)

    for bad in ["superuser", "Owner", "", "data_provider;drop"]:
        resp = await client.patch(f"/api/v1/auth/users/{target.id}/role",
                                  json={"role": bad, "reason": "test role change", "ticket_id": "T-1"},
                                  headers=admin_headers)
        assert resp.status_code == 400, f"{bad!r}: {resp.text}"
    # role unchanged
    resp = await client.get("/api/v1/auth/me", headers=_headers(target))
    assert resp.json()["role"] == "buyer"


@pytest.mark.asyncio
async def test_role_patch_valid_enum_accepted(client: AsyncClient, db_session: AsyncSession):
    admin = await _create_user(db_session, role="admin", prefix="pz10admin")
    target = await _create_user(db_session, role="buyer", prefix="pz10tgt")
    admin_headers = _headers(admin)

    resp = await client.patch(f"/api/v1/auth/users/{target.id}/role",
                              json={"role": "data_provider",
                                    "reason": "business role adjustment",
                                    "ticket_id": "TICKET-42"},
                              headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "data_provider"
    # P-GAP-014: reason + ticket_id must land in the audit trail.
    from sqlalchemy import select as _sel

    from app.models.audit_log import AuditLog

    row = (await db_session.execute(
        _sel(AuditLog).where(
            AuditLog.action == "user.role_changed",
            AuditLog.resource_id == str(target.id),
        ).order_by(AuditLog.created_at.desc()).limit(1)
    )).scalars().first()
    assert row is not None, "role change must be audited"
    assert row.detail.get("reason") == "business role adjustment"
    assert row.detail.get("ticket_id") == "TICKET-42"
    assert row.detail.get("old_role") == "buyer"
    assert row.detail.get("new_role") == "data_provider"


@pytest.mark.asyncio
async def test_role_patch_reason_required(client: AsyncClient, db_session: AsyncSession):
    """PZ-10 high-risk contract: missing or short reason is rejected (422)."""
    admin = await _create_user(db_session, role="admin", prefix="pz10admin")
    target = await _create_user(db_session, role="buyer", prefix="pz10tgt")
    admin_headers = _headers(admin)

    # missing reason → validation error
    resp = await client.patch(f"/api/v1/auth/users/{target.id}/role",
                              json={"role": "admin"}, headers=admin_headers)
    assert resp.status_code == 422, resp.text
    # short reason (<8 chars) → validation error
    resp = await client.patch(f"/api/v1/auth/users/{target.id}/role",
                              json={"role": "admin", "reason": "short"}, headers=admin_headers)
    assert resp.status_code == 422, resp.text
    # role unchanged
    resp = await client.get("/api/v1/auth/me", headers=_headers(target))
    assert resp.json()["role"] == "buyer"


@pytest.mark.asyncio
async def test_role_patch_admin_only(client: AsyncClient, db_session: AsyncSession):
    buyer = await _create_user(db_session, role="buyer", prefix="pz10a")
    other = await _create_user(db_session, role="buyer", prefix="pz10b")
    resp = await client.patch(f"/api/v1/auth/users/{other.id}/role",
                              json={"role": "admin", "reason": "escalation attempt"},
                              headers=_headers(buyer))
    assert resp.status_code == 403


# ── Register role whitelist (PZ-10 tightening) ──────────────────────

@pytest.mark.asyncio
async def test_register_non_admin_role_rejected(client: AsyncClient):
    unique = uuid.uuid4().hex[:8]
    for role in ("buyer", "data_provider", "operator", "regulator"):
        resp = await client.post("/api/v1/auth/register", json={
            "username": f"pz10reg_{role}_{unique}",
            "email": f"pz10reg_{role}_{unique}@example.com",
            "password": PASSWORD,
            "role": role,
        })
        assert resp.status_code == 422, f"{role}: {resp.text}"


@pytest.mark.asyncio
async def test_register_admin_self_selectable_and_default_buyer(client: AsyncClient):
    unique = uuid.uuid4().hex[:8]
    resp = await client.post("/api/v1/auth/register", json={
        "username": f"pz10adm_{unique}",
        "email": f"pz10adm_{unique}@example.com",
        "password": PASSWORD,
        "role": "admin",
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["user"]["role"] == "admin"

    resp = await client.post("/api/v1/auth/register", json={
        "username": f"pz10def_{unique}",
        "email": f"pz10def_{unique}@example.com",
        "password": PASSWORD,
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["user"]["role"] == "buyer"


# ── Migration 0012 ───────────────────────────────────────────────────

@pytest.mark.slow
def test_migration_0012_user_mfa_up(tmp_path):
    """alembic upgrade head builds user_mfa with the exact PZ-10 column set."""
    db = tmp_path / "pz10.db"
    env = {**os.environ, "CDS_DATABASE_URL": f"sqlite+aiosqlite:///{db}"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    engine = create_engine(f"sqlite:///{db}")
    inspector = inspect(engine)
    assert "user_mfa" in inspector.get_table_names()
    cols = {c["name"] for c in inspector.get_columns("user_mfa")}
    assert cols == {
        "user_id", "totp_secret_encrypted", "confirmed",
        "recovery_codes_encrypted", "created_at",
    }


# ── SM2 cert login second factor ─────────────────────────────────────

def _build_test_cert(private_key: str, public_key: str, subject_cn: str) -> str:
    """Real cert via the existing certificate service (SM2-with-SM3 signed)."""
    from app.services.certificate_service import certificate_service

    now = datetime.now(timezone.utc)
    return certificate_service._build_cert_pem(
        serial=f"pz10{uuid.uuid4().hex[:12]}",
        subject=f"CN={subject_cn},O=CDS,C=CN",
        issuer="CN=CDS Pilot CA,C=CN",
        public_key=public_key,
        not_before=now - timedelta(hours=1),
        not_after=now + timedelta(days=30),
        is_ca=False,
        private_key=private_key,
    )


async def _cert_challenge(client: AsyncClient, username: str) -> str:
    """Fetch a server-issued single-use challenge (B1: a client-chosen nonce is
    no longer accepted by /auth/login/cert)."""
    resp = await client.post("/api/v1/auth/login/cert/challenge", json={"username": username})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["expires_in"] > 0
    return body["challenge"]


@pytest.mark.asyncio
async def test_cert_login_happy_path(client: AsyncClient, db_session: AsyncSession):
    from app.services.crypto_service import crypto_service

    keypair = crypto_service.generate_keypair()
    unique = uuid.uuid4().hex[:8]
    user = User(
        username=f"pz10cert_{unique}",
        email=f"pz10cert_{unique}@example.com",
        hashed_password="unused-for-cert-login",
        role="buyer",
        sm2_certificate=keypair.public_key,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)

    cert_pem = _build_test_cert(keypair.private_key, keypair.public_key, user.username)
    nonce = await _cert_challenge(client, user.username)
    sig = crypto_service.sign(nonce.encode("utf-8"), keypair.private_key, keypair.public_key)

    resp = await client.post("/api/v1/auth/login/cert", json={
        "username": user.username,
        "cert_pem": cert_pem,
        "nonce": nonce,
        "signature": sig.signature,
    })
    assert resp.status_code == 200, resp.text
    assert "access_token" in resp.json()
    assert resp.json()["user"]["username"] == user.username

    # B1 regression: this identical body is a captured request — the challenge
    # is already spent, so the replay must be rejected.
    replay = await client.post("/api/v1/auth/login/cert", json={
        "username": user.username,
        "cert_pem": cert_pem,
        "nonce": nonce,
        "signature": sig.signature,
    })
    assert replay.status_code == 401, replay.text
    assert "challenge" in str(replay.json()["detail"]).lower()


@pytest.mark.asyncio
async def test_cert_login_rejects_wrong_signature(client: AsyncClient, db_session: AsyncSession):
    from app.services.crypto_service import crypto_service

    keypair = crypto_service.generate_keypair()
    unique = uuid.uuid4().hex[:8]
    user = User(
        username=f"pz10cert2_{unique}",
        email=f"pz10cert2_{unique}@example.com",
        hashed_password="unused-for-cert-login",
        role="buyer",
        sm2_certificate=keypair.public_key,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)

    cert_pem = _build_test_cert(keypair.private_key, keypair.public_key, user.username)
    # sign a DIFFERENT message than the challenge presented
    nonce = await _cert_challenge(client, user.username)
    sig = crypto_service.sign(b"pz10-nonce-other", keypair.private_key, keypair.public_key)
    resp = await client.post("/api/v1/auth/login/cert", json={
        "username": user.username,
        "cert_pem": cert_pem,
        "nonce": nonce,
        "signature": sig.signature,
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_cert_login_rejects_unbound_certificate(client: AsyncClient, db_session: AsyncSession):
    """A valid cert whose key does not match the account's stored SM2 key is
    rejected — possession of an unrelated key is not a credential."""
    from app.services.crypto_service import crypto_service

    account_key = crypto_service.generate_keypair()
    foreign_key = crypto_service.generate_keypair()
    unique = uuid.uuid4().hex[:8]
    user = User(
        username=f"pz10cert3_{unique}",
        email=f"pz10cert3_{unique}@example.com",
        hashed_password="unused-for-cert-login",
        role="buyer",
        sm2_certificate=account_key.public_key,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)

    cert_pem = _build_test_cert(foreign_key.private_key, foreign_key.public_key, user.username)
    nonce = await _cert_challenge(client, user.username)
    sig = crypto_service.sign(nonce.encode("utf-8"), foreign_key.private_key, foreign_key.public_key)
    resp = await client.post("/api/v1/auth/login/cert", json={
        "username": user.username,
        "cert_pem": cert_pem,
        "nonce": nonce,
        "signature": sig.signature,
    })
    assert resp.status_code == 401
    assert "bound" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_cert_login_disabled_via_setting(client: AsyncClient, monkeypatch):
    # Patch the instance the endpoint actually reads: auth.py binds `settings =
    # get_settings()` at module level. In the full suite, test_k8s_n7's
    # get_settings.cache_clear() (alphabetically earlier) makes get_settings()
    # return a FRESH instance, so patching get_settings()'s result would miss
    # the endpoint's binding → 401 instead of 403. Patch auth_mod.settings
    # directly: deterministic regardless of cache state.
    import app.api.auth as auth_mod

    monkeypatch.setattr(auth_mod.settings, "AUTH_CERT_LOGIN", False)
    resp = await client.post("/api/v1/auth/login/cert", json={
        "username": "nobody",
        "cert_pem": "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----",
        "nonce": "pz10-nonce-disabled",
        "signature": "00" * 64,
    })
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_cert_login_rejects_client_chosen_nonce(client: AsyncClient, db_session: AsyncSession):
    """B1: a perfectly valid cert + signature over an ATTACKER-CHOSEN nonce must
    not log anybody in — that was the replayable shape of the old contract."""
    from app.services.crypto_service import crypto_service

    keypair = crypto_service.generate_keypair()
    unique = uuid.uuid4().hex[:8]
    user = User(
        username=f"pz10cert4_{unique}",
        email=f"pz10cert4_{unique}@example.com",
        hashed_password="unused-for-cert-login",
        role="buyer",
        sm2_certificate=keypair.public_key,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)

    cert_pem = _build_test_cert(keypair.private_key, keypair.public_key, user.username)
    nonce = f"attacker-chosen-{uuid.uuid4().hex}"
    sig = crypto_service.sign(nonce.encode("utf-8"), keypair.private_key, keypair.public_key)
    resp = await client.post("/api/v1/auth/login/cert", json={
        "username": user.username,
        "cert_pem": cert_pem,
        "nonce": nonce,
        "signature": sig.signature,
    })
    assert resp.status_code == 401, resp.text
    assert "challenge" in str(resp.json()["detail"]).lower()


@pytest.mark.asyncio
async def test_cert_login_rejects_challenge_of_other_account(client: AsyncClient,
                                                            db_session: AsyncSession):
    """The challenge is bound to a username: spending Alice's challenge on
    Bob's credentials is rejected before any credential is checked."""
    from app.services.crypto_service import crypto_service

    keypair = crypto_service.generate_keypair()
    unique = uuid.uuid4().hex[:8]
    user = User(
        username=f"pz10cert5_{unique}",
        email=f"pz10cert5_{unique}@example.com",
        hashed_password="unused-for-cert-login",
        role="buyer",
        sm2_certificate=keypair.public_key,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)

    cert_pem = _build_test_cert(keypair.private_key, keypair.public_key, user.username)
    nonce = await _cert_challenge(client, f"someone-else-{unique}")
    sig = crypto_service.sign(nonce.encode("utf-8"), keypair.private_key, keypair.public_key)
    resp = await client.post("/api/v1/auth/login/cert", json={
        "username": user.username,
        "cert_pem": cert_pem,
        "nonce": nonce,
        "signature": sig.signature,
    })
    assert resp.status_code == 401, resp.text
    assert "belong" in str(resp.json()["detail"]).lower()
