"""N6 — sandbox_tasks rag params + rag_generative_models table (RAG phase 2).

Revision ID: 0010_rag_generative
Revises: 0009_trained_models
Create Date: 2026-09-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0010_rag_generative"
down_revision = "0009_trained_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sandbox_tasks", sa.Column("rag_answer_mode", sa.String(length=32), nullable=True))
    op.add_column("sandbox_tasks", sa.Column("rag_generative_model_id", sa.String(length=64), nullable=True))

    op.create_table(
        "rag_generative_models",
        sa.Column("model_id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("artifact_ref", sa.String(length=512), nullable=False),
        sa.Column("vocab_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("rag_generative_models")
    op.drop_column("sandbox_tasks", "rag_generative_model_id")
    op.drop_column("sandbox_tasks", "rag_answer_mode")
