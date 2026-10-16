"""CB-05 — cube template registry tables (SS-10, spec §6.2 m2):
``cube_templates`` (template/artifact registry row incl. probe config) +
``cube_template_nodes`` (per-node distribution state machine). Additive-only.

Revision ID: 0014_cube_templates
Revises: 0013_volume_keys
Create Date: 2026-10-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0014_cube_templates"
down_revision = "0013_volume_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cube_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False, server_default="1.0.0"),
        sa.Column("artifact_id", sa.String(length=128), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("image_ref", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="BUILDING"),
        sa.Column("probe_config", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("name", "version", name="uq_cube_templates_name_version"),
    )
    op.create_index("ix_cube_templates_name", "cube_templates", ["name"])
    op.create_index("ix_cube_templates_artifact_id", "cube_templates", ["artifact_id"], unique=True)
    op.create_index("ix_cube_templates_status", "cube_templates", ["status"])

    op.create_table(
        "cube_template_nodes",
        sa.Column("template_id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("node_id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["cube_templates.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_cube_template_nodes_node_status", "cube_template_nodes", ["node_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_cube_template_nodes_node_status", table_name="cube_template_nodes")
    op.drop_table("cube_template_nodes")
    op.drop_index("ix_cube_templates_status", table_name="cube_templates")
    op.drop_index("ix_cube_templates_artifact_id", table_name="cube_templates")
    op.drop_index("ix_cube_templates_name", table_name="cube_templates")
    op.drop_table("cube_templates")
