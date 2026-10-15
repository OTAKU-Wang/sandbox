import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class UserMFA(Base):
    """PZ-10: per-user MFA (TOTP) state.

    ``totp_secret_encrypted`` / ``recovery_codes_encrypted`` hold SM4-GCM
    encrypted blobs (base64 of nonce+tag+ciphertext, see
    ``app.api.auth._mfa_cipher``) — plaintext secrets never touch the table.
    MFA login enforcement only applies while ``confirmed`` is true; a row in
    flight (enrolled but unverified) does not block password login.
    """

    __tablename__ = "user_mfa"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    totp_secret_encrypted: Mapped[str | None] = mapped_column(String(512), nullable=True)
    confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    recovery_codes_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
