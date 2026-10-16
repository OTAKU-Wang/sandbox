"""SS-12 — platform API keys (machine credentials with functional scope domains).

Additive-only: new table `api_keys` (spec ss-12-agent-api-keys.md §4.1/§5).
The bare key is never persisted — only its SM3 hash (unique lookup) and a
display prefix. Revocation is a status flip + forensic columns; rows are
never physically deleted.

Note (spec §4.5 write-back): the activity query path
(resource_type, resource_id) is already covered by the existing
`ix_audit_logs_resource` index on audit_logs — no additive audit index needed.

Revision ID: 0017_api_keys
Revises: 0016_sandbox_tee_policy
Create Date: 2026-10-13
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0017_api_keys"
down_revision = "0016_sandbox_tee_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("key_prefix", sa.String(length=16), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("rate_limit", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("issued_by", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
        sa.ForeignKeyConstraint(["issued_by"], ["users.id"]),
    )
    op.create_index("ix_api_keys_status", "api_keys", ["status"], unique=False)
    op.create_index("ix_api_keys_issued_by", "api_keys", ["issued_by"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_api_keys_issued_by", table_name="api_keys")
    op.drop_index("ix_api_keys_status", table_name="api_keys")
    op.drop_table("api_keys")
