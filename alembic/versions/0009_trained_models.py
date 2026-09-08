"""N5 — trained_models + inference_usage tables (inference service sandbox).

Revision ID: 0009_trained_models
Revises: 0008_mpc_persistence
Create Date: 2026-09-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0009_trained_models"
down_revision = "0008_mpc_persistence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trained_models",
        sa.Column("model_id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("task_id", sa.String(length=255), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("format", sa.String(length=32), nullable=False),
        sa.Column("artifact_ref", sa.String(length=512), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="registered"),
        sa.Column("watermark_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_trained_models_status", "trained_models", ["status"])
    op.create_index("ix_trained_models_product", "trained_models", ["product_id"])

    op.create_table(
        "inference_usage",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contract_id", sa.String(length=64), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.String(length=255), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("epsilon_consumed", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="succeeded"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_index("ix_inference_usage_model", "inference_usage", ["model_id"])
    op.create_index("ix_inference_usage_contract", "inference_usage", ["contract_id"])


def downgrade() -> None:
    op.drop_index("ix_inference_usage_contract", table_name="inference_usage")
    op.drop_index("ix_inference_usage_model", table_name="inference_usage")
    op.drop_table("inference_usage")
    op.drop_index("ix_trained_models_product", table_name="trained_models")
    op.drop_index("ix_trained_models_status", table_name="trained_models")
    op.drop_table("trained_models")
