import asyncio
import logging
import threading
import uuid
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt
import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings, _JWT_DEFAULT_KEY
from app.models.user import User

settings = get_settings()
logger = logging.getLogger(__name__)

# bcrypt 5.0+ enforces 72-byte limit. passlib 1.7.4 is incompatible with bcrypt 5.0+.
# Use bcrypt directly for password hashing.

_BCRYPT_MAX_BYTES = 72
_LOCAL_DEFAULT_JWT_KEY = "cds-local-dev-test-jwt-secret-key-32-bytes-minimum"
# A89: 这个常量连同下面的默认密钥都是公开的，只有显式开关打开时才允许拿它签名。
_LOCAL_DEFAULT_JWT_KEY_ENV = "CDS_ALLOW_LOCAL_DEFAULT_JWT_KEY"

_ephemeral_jwt_key: str | None = None
_EPHEMERAL_JWT_LOCK = threading.Lock()
_JWT_KEY_WARNINGS: set[str] = set()


def _env_flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _jwt_key_disclosure(kind: str, detail: str) -> None:
    if kind in _JWT_KEY_WARNINGS:
        return
    _JWT_KEY_WARNINGS.add(kind)
    logger.warning("JWT signing key: %s — %s", kind, detail)


def _truncate(password: str) -> bytes:
    """Truncate password to 72 bytes for bcrypt."""
    encoded = password.encode("utf-8")
    return encoded[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_truncate(password), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(_truncate(plain), hashed.encode("utf-8"))


async def hash_password_async(password: str) -> str:
    """A92: bcrypt at rounds=12 costs 200-300ms of CPU; keep it off the event loop."""
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(plain: str, hashed: str) -> bool:
    """A92: login-path hashing must not stall every other request on this worker."""
    return await asyncio.to_thread(verify_password, plain, hashed)


def _effective_jwt_secret_key() -> str:
    """Resolve the signing key, never silently falling back to the published one.

    A89: `_LOCAL_DEFAULT_JWT_KEY` lives in the source tree, so any stack that signed
    with it let anyone holding the source mint a `role=admin` token. Default compose
    and both deploy scripts ship `CDS_DEBUG=true`, so DEBUG alone must not unlock it:
    a DEBUG stack without a configured secret now signs with a per-process random key
    (disclosed via WARNING), a fixed local key requires the explicit opt-in switch,
    and production still fails closed.
    """
    global _ephemeral_jwt_key

    if settings.JWT_SECRET_KEY != _JWT_DEFAULT_KEY:
        return settings.JWT_SECRET_KEY

    if os.environ.get("TESTING") == "1" or os.environ.get("PYTEST_CURRENT_TEST"):
        return _LOCAL_DEFAULT_JWT_KEY

    if _env_flag(_LOCAL_DEFAULT_JWT_KEY_ENV):
        _jwt_key_disclosure(
            "the source-published development key",
            f"{_LOCAL_DEFAULT_JWT_KEY_ENV} is enabled: anyone with the source can forge "
            "tokens. Set CDS_JWT_SECRET_KEY instead.",
        )
        return _LOCAL_DEFAULT_JWT_KEY

    if settings.DEBUG:
        with _EPHEMERAL_JWT_LOCK:
            if _ephemeral_jwt_key is None:
                _ephemeral_jwt_key = secrets.token_urlsafe(48)
                _jwt_key_disclosure(
                    "an ephemeral per-process JWT key",
                    "CDS_JWT_SECRET_KEY is unset while DEBUG is on: tokens are invalid "
                    "across restarts/replicas. Set CDS_JWT_SECRET_KEY for a stable stack.",
                )
        return _ephemeral_jwt_key

    settings.validate_jwt_security()
    return settings.JWT_SECRET_KEY


def create_access_token(user_id: uuid.UUID, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "role": role,
        "exp": expire,
        "type": "access",
    }
    return jwt.encode(payload, _effective_jwt_secret_key(), algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(user_id: uuid.UUID) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=7)
    payload = {
        "sub": str(user_id),
        "exp": expire,
        "type": "refresh",
    }
    return jwt.encode(payload, _effective_jwt_secret_key(), algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    payload = jwt.decode(token, _effective_jwt_secret_key(), algorithms=[settings.JWT_ALGORITHM])
    if payload.get("type") != "access":
        raise ValueError("Invalid token type")
    return payload


def decode_refresh_token(token: str) -> dict:
    payload = jwt.decode(token, _effective_jwt_secret_key(), algorithms=[settings.JWT_ALGORITHM])
    if payload.get("type") != "refresh":
        raise ValueError("Invalid token type")
    return payload


# --- CR-2026-10-15 B1: server-issued single-use certificate-login challenge ---

CERT_CHALLENGE_TTL_SECONDS = 300


def create_cert_challenge(username: str) -> tuple[str, int]:
    """Mint a certificate-login challenge bound to ``username``.

    The token is self-contained and signed, so every worker can verify it
    without shared state; single use is enforced separately by
    ``app.core.challenge_store``. A client-chosen nonce is NOT accepted by
    ``/auth/login/cert`` any more: without a server-issued, consumed challenge
    a captured request body could be replayed forever (the signature covers
    whatever nonce the attacker picked), which would bypass the password and
    TOTP factors this login path replaces.
    """
    expire = datetime.now(timezone.utc) + timedelta(seconds=CERT_CHALLENGE_TTL_SECONDS)
    payload = {
        "sub": username,
        "nonce": secrets.token_urlsafe(24),
        "exp": expire,
        "type": "cert_challenge",
    }
    token = jwt.encode(payload, _effective_jwt_secret_key(), algorithm=settings.JWT_ALGORITHM)
    return token, CERT_CHALLENGE_TTL_SECONDS


def decode_cert_challenge(token: str) -> dict:
    """Decode + verify a cert-login challenge; raise ValueError if not one."""
    payload = jwt.decode(token, _effective_jwt_secret_key(), algorithms=[settings.JWT_ALGORITHM])
    if payload.get("type") != "cert_challenge":
        raise ValueError("Invalid token type")
    return payload


async def register_user(db: AsyncSession, username: str, email: str, password: str, role: str = "buyer", organization: str | None = None) -> User:
    # Check for existing user
    existing = await db.execute(select(User).where((User.username == username) | (User.email == email)))
    if existing.scalar_one_or_none():
        raise ValueError("Username or email already exists")

    user = User(
        username=username,
        email=email,
        hashed_password=await hash_password_async(password),
        role=role,
        organization=organization,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def authenticate_user(db: AsyncSession, username: str, password: str) -> User | None:
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user and await verify_password_async(password, user.hashed_password):
        return user
    return None


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()
