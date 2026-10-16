"""PZ-03 — chain_anchoring_records table (SS-11 §7.2, FG-005 software side).

Revision ID: 0011_chain_anchoring_records
Revises: 0010_rag_generative
Create Date: 2026-10-12
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0011_chain_anchoring_records"
down_revision = "0010_rag_generative"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chain_anchoring_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("audit_record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("backend", sa.String(length=32), nullable=False),
        sa.Column("tx_hash", sa.String(length=128), nullable=True),
        sa.Column("block_height", sa.Integer(), nullable=True),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("receipt", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_index("ix_chain_anchoring_audit_record", "chain_anchoring_records", ["audit_record_id"], unique=False)
    op.create_index("ix_chain_anchoring_backend", "chain_anchoring_records", ["backend"], unique=False)
    op.create_index("ix_chain_anchoring_created", "chain_anchoring_records", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_chain_anchoring_created", table_name="chain_anchoring_records")
    op.drop_index("ix_chain_anchoring_backend", table_name="chain_anchoring_records")
    op.drop_index("ix_chain_anchoring_audit_record", table_name="chain_anchoring_records")
    op.drop_table("chain_anchoring_records")
