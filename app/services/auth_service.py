import uuid
from datetime import datetime, timedelta, timezone

import jwt
import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.user import User

settings = get_settings()

# bcrypt 5.0+ enforces 72-byte limit. passlib 1.7.4 is incompatible with bcrypt 5.0+.
# Use bcrypt directly for password hashing.

_BCRYPT_MAX_BYTES = 72


def _truncate(password: str) -> bytes:
    """Truncate password to 72 bytes for bcrypt."""
    encoded = password.encode("utf-8")
    return encoded[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_truncate(password), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(_truncate(plain), hashed.encode("utf-8"))


def create_access_token(user_id: uuid.UUID, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "role": role,
        "exp": expire,
        "type": "access",
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(user_id: uuid.UUID) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=7)
    payload = {
        "sub": str(user_id),
        "exp": expire,
        "type": "refresh",
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    if payload.get("type") != "access":
        raise ValueError("Invalid token type")
    return payload


def decode_refresh_token(token: str) -> dict:
    payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    if payload.get("type") != "refresh":
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
        hashed_password=hash_password(password),
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
    if user and verify_password(password, user.hashed_password):
        return user
    return None


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()
