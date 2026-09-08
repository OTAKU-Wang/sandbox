"""N3 — mpc_keys + mpc_key_shares tables (Shamir share persistence).

Revision ID: 0008_mpc_persistence
Revises: 0007_shared_volumes
Create Date: 2026-09-08
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0008_mpc_persistence"
down_revision = "0007_shared_volumes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mpc_keys",
        sa.Column("key_id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("algorithm", sa.String(length=16), nullable=False, server_default="sm4"),
        sa.Column("threshold", sa.Integer(), nullable=False),
        sa.Column("total_shares", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_mpc_keys_status", "mpc_keys", ["status"])

    op.create_table(
        "mpc_key_shares",
        sa.Column("share_id", sa.String(length=128), primary_key=True, nullable=False),
        sa.Column("key_id", sa.String(length=64), nullable=False),
        sa.Column("share_index", sa.Integer(), nullable=False),
        sa.Column("share_value", sa.LargeBinary(), nullable=False),
        sa.Column("holder_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["key_id"], ["mpc_keys.key_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_mpc_shares_key", "mpc_key_shares", ["key_id"])


def downgrade() -> None:
    op.drop_index("ix_mpc_shares_key", table_name="mpc_key_shares")
    op.drop_table("mpc_key_shares")
    op.drop_index("ix_mpc_keys_status", table_name="mpc_keys")
    op.drop_table("mpc_keys")
