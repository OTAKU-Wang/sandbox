"""Refresh-session registry — server-side revocation for refresh tokens (CR-2026-10-15 §4.10 / M3).

Before this table existed, `/auth/refresh` only verified the JWT signature and
``user.is_active``: an issued refresh token could not be voided inside its
7-day validity window, and an MFA-enabled account could keep renewing sessions
with a stolen pre-MFA refresh token forever (no second factor re-check).

One row is written per successful login (password+MFA gate, register, cert
login) and on every refresh rotation; the row's ``jti`` is the JWT id embedded
in the refresh token. Revocation (logout, rotation) flips ``revoked`` — rows
are never deleted, the trail is the point. ``revoked`` semantics:

* rotation: the pre-rotation session is marked revoked atomically with the new
  row's insert (single-use refresh tokens);
* logout: the presented session is revoked;
* expiry is enforced on read from ``expires_at`` (no janitor dependency).

No secret material lives here: the jti is an opaque random id, not a credential
by itself — presenting it still requires the signature-valid token carrying it.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class RefreshSession(Base):
    __tablename__ = "refresh_sessions"
    __table_args__ = (
        Index("ix_refresh_sessions_user_id", "user_id"),
        Index("ix_refresh_sessions_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # JWT id carried by the refresh token; one unique row per issued token.
    jti: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # Server-side record of when the session was minted. The MFA re-verification
    # gate compares this against user_mfa.confirmed_at: a session issued BEFORE
    # the user's MFA was confirmed cannot survive refresh once MFA is on.
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc), server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
