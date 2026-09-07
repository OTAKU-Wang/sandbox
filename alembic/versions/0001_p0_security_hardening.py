"""P0 security hardening — additive columns for gap A1/A3/B3.

Revision ID: 0001_p0_security_hardening
Revises: 0000_baseline_all_tables (chain root — creates all live tables)
Create Date: 2026-09-06

NOTE ON THIS MIGRATION
----------------------
The repository historically created schema via Base.metadata.create_all
(dev/test) with no Alembic version chain (no alembic/versions existed). This
is the first migration; it only ADDS new nullable columns for the P0 gap
remediation — it is safe to run against a database already created by
create_all, and against an empty database it is harmless.

- contracts.valid_until          (A1: contract expiry sweep)
- contracts.purpose*             (P1 design alignment; added here to keep the
                                  chain additive in one migration)
- key_metadata.wrapped_payload   (B3: wrapped-key restart recovery) — note the
                                  live model is app/models/kms.DataEncryptionKey
- sandbox_sessions.contract_index — not needed; contract_id already indexed
- policy_bundles.revoked_at      (A1: bundle revocation marker)

For databases bootstrapped with create_all, run `alembic stamp head` after
upgrading so the chain tracks the applied state.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0001_p0_security_hardening"
down_revision = "0000_baseline_all_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A1: contract expiry deadline
    op.add_column("contracts", sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True))
    # A4 (P1): purpose limitation fields
    op.add_column("contracts", sa.Column("purpose", sa.String(length=64), nullable=True))
    op.add_column("contracts", sa.Column("purpose_scope", sa.JSON(), nullable=True))
    op.add_column("sandbox_tasks", sa.Column("purpose", sa.String(length=64), nullable=True))
    # A5/T7: field-level classification map on data resources
    op.add_column("data_resources", sa.Column("field_classifications", sa.JSON(), nullable=True))
    # B3: wrapped-key persistence (live model: data_encryption_keys)
    op.add_column("data_encryption_keys", sa.Column("wrapped_payload", sa.LargeBinary(), nullable=True))
    op.add_column("data_encryption_keys", sa.Column("sm2_encrypted_payload", sa.LargeBinary(), nullable=True))
    # A1: policy bundle revocation marker
    op.add_column("policy_bundles", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("policy_bundles", "revoked_at")
    op.drop_column("data_encryption_keys", "sm2_encrypted_payload")
    op.drop_column("data_encryption_keys", "wrapped_payload")
    op.drop_column("sandbox_tasks", "purpose")
    op.drop_column("data_resources", "field_classifications")
    op.drop_column("contracts", "purpose_scope")
    op.drop_column("contracts", "purpose")
    op.drop_column("contracts", "valid_until")
