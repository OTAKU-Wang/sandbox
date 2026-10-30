"""Model watermark record — one per watermarked training job.

A186 (at-rest half): ``key_seed`` is watermark location key material. The
finding (docs/ocr-review-2026-10-18/ocr-verified-findings.md ``### A186``)
recorded it as a plaintext ``INTEGER`` column: anyone who could read the DB
held the seed. The other two legs landed before this one — position selection
is derived from ``SM3(context | server pepper | key_seed | layer names)``
(``app/services/model_watermark.py``) so the seed alone no longer locates the
marks, and the training API returns only the irreversible
``key_seed_fingerprint`` (``app/api/training.py``, A186 server leg). This
closes the remaining one: the column persists ONLY a labeled SM4-GCM sealed
blob — ``cds-ks1:`` + base64 of the canonical ``[nonce(12)|tag(16)|ct]``
envelope built with ``app.utils.crypto.SM4Cipher`` — under a domain-separated
KEK (HMAC over a purpose label off the deployment master secret, same pattern
as the MPC share KEK in ``app/services/mpc_service.py``), so the seed KEK and
the audit/share KEKs rotate independently.

The column type deliberately derives from ``sqlalchemy.types.BigInteger``:
``app/api/training.py::_key_seed_column_max`` derives the accepted seed range
from the column width and refuses to guess for any type it does not know. A186
shipped it deriving from ``Integer`` (the storage encoding changed, the domain
did not); A186b widens the *logical* domain to BIGINT — 0…2**63-1 — because the
finding's core consequence is entropy (「水印的全部安全性 = 这颗 31-bit 种子」).
The physical column stays the width-free ``TEXT`` blob of revision 0030, so no
accepted value can overflow it, and revision ``0031_ocr_seed_bigint`` is what
proves/enforces that storage shape on an already-migrated database.
"""
import base64
import hashlib
import hmac
import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, BigInteger, Integer, Text, TypeDecorator, func, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base

# Blob label = scheme version tag carried in every stored value (mirrors the
# _SHARE_KEK_TAG pattern in app/services/mpc_service.py).
_SEED_LABEL = "cds-ks1:"
# Domain separation: the seed KEK must not equal any other consumer's KEK that
# hangs off the same master secret.
_SEED_KEK_INFO = b"cds-watermark-key-seed-v1"


def _seed_kek() -> bytes:
    """Domain-separated KEK for key_seed at-rest encryption (A186)."""
    from app.core.config import get_settings

    s = get_settings()
    master = s.AUDIT_ENCRYPTION_KEY or s.JWT_SECRET_KEY
    return hmac.new(master.encode("utf-8"), _SEED_KEK_INFO, hashlib.sha256).digest()


def encrypt_key_seed(seed: int) -> str:
    """Seal an integer seed into the labeled ``cds-ks1:`` blob (A186)."""
    from app.utils.crypto import SM4Cipher

    cipher = SM4Cipher(key=_seed_kek())
    ct, nonce, tag = cipher.encrypt_gcm(str(seed).encode("ascii"), associated_data=_SEED_KEK_INFO)
    return _SEED_LABEL + base64.b64encode(nonce + tag + ct).decode("ascii")


def decrypt_key_seed(blob: str) -> int:
    """Open a ``cds-ks1:`` blob back into the integer seed."""
    from app.utils.crypto import SM4Cipher

    body = base64.b64decode(blob[len(_SEED_LABEL):], validate=True)
    nonce, tag, ct = body[:12], body[12:28], body[28:]
    cipher = SM4Cipher(key=_seed_kek())
    return int(cipher.decrypt_gcm(ct, nonce, tag, associated_data=_SEED_KEK_INFO).decode("ascii"))


class EncryptedKeySeed(TypeDecorator, BigInteger):
    """BIGINT-semantics column whose on-disk encoding is a sealed blob (A186/A186b).

    Storage is ``Text`` (the sealed blob), but the type derives from
    ``BigInteger`` because ``app/api/training.py`` fail-closes on column types it
    cannot width-check: the accepted seed domain is the BIGINT domain
    (0…2**63-1) since A186b — the 31-bit seed was the finding's entropy defect —
    while storage stays the width-free ciphertext blob, so the bound
    ``_key_seed_column_max()`` reports is both real and storable. Reads tolerate
    legacy unsealed integers written before revision 0030 (disclosed
    pass-through) but reject anything that is neither a blob nor an integer —
    never a silent wrong value.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, str) and value.startswith(_SEED_LABEL):
            return value  # already sealed (e.g. revision 0030 wrote it)
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"key_seed must be an int, got {type(value).__name__}")
        return encrypt_key_seed(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, int):
            return value  # legacy pre-encryption row (disclosed pass-through)
        s = str(value)
        if s.startswith(_SEED_LABEL):
            return decrypt_key_seed(s)
        try:
            return int(s)  # legacy plaintext row (disclosed pass-through)
        except ValueError:
            raise ValueError(
                "watermark_records.key_seed holds neither a cds-ks1: blob nor a "
                "plain integer — refusing to guess the seed"
            )


class WatermarkRecord(Base):
    """Watermark embedding record per training job."""
    __tablename__ = "watermark_records"
    __table_args__ = (
        Index("ix_watermark_records_owner", "owner_id"),
        # A171: app/api/training.py reads this by job_id with
        # scalar_one_or_none(); without uniqueness the check-then-act race put
        # two rows on one job and both the embed pre-check and the verify
        # endpoint then raised forever.
        Index("ix_watermark_records_job_id", "job_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    layer_positions: Mapped[dict] = mapped_column(JSON, nullable=False)
    ordered_positions: Mapped[list] = mapped_column(JSON, nullable=False)
    # A186: secret material — sealed at rest (see EncryptedKeySeed), never a
    # plaintext integer (AGENTS.md model rule: "Never add a plaintext
    # key/secret column"). A186b: the logical seed domain is BIGINT (0…2**63-1),
    # not the historical 31-bit Integer.
    key_seed: Mapped[int] = mapped_column(EncryptedKeySeed(), nullable=False)
    num_bits: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
