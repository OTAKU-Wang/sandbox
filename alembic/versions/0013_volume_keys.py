"""CB-01 — volume_keys table (SS-10 CB-03): plugin-generated, KEK-wrapped
volume DEK lifecycle backing the Go volume plugin's control-plane KMS surface
(POST/DELETE /api/v1/kms/volume-keys*). Additive-only.

Revision ID: 0013_volume_keys
Revises: 0012_user_mfa
Create Date: 2026-10-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0013_volume_keys"
down_revision = "0012_user_mfa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "volume_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("key_id", sa.String(length=255), nullable=False, index=True, unique=True),
        sa.Column("volume_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("algorithm", sa.String(length=32), nullable=False, server_default="SM4-GCM"),
        sa.Column("key_length", sa.Integer(), nullable=False, server_default="16"),
        sa.Column("wrapped_key", sa.LargeBinary(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_volume_keys_volume_id", "volume_keys", ["volume_id"])
    op.create_index("ix_volume_keys_status", "volume_keys", ["status"])


def downgrade() -> None:
    op.drop_index("ix_volume_keys_status", table_name="volume_keys")
    op.drop_index("ix_volume_keys_volume_id", table_name="volume_keys")
    op.drop_table("volume_keys")
