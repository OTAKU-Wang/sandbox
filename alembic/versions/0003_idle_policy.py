"""W9 — idle auto-pause policy columns on sandbox_sessions.

Revision ID: 0003_idle_policy
Revises: 0002_session_usability
Create Date: 2026-09-07

Additive-only, safe against a create_all database:

- sandbox_sessions.idle_policy   ("kill" | "pause", NULL = default policy)
- sandbox_sessions.auto_resume   (wake a suspended session on interaction,
                                  gated on the governing contract being ACTIVE)
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0003_idle_policy"
down_revision = "0002_session_usability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sandbox_sessions", sa.Column("idle_policy", sa.String(length=16), nullable=True))
    op.add_column(
        "sandbox_sessions",
        sa.Column("auto_resume", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("sandbox_sessions", "auto_resume")
    op.drop_column("sandbox_sessions", "idle_policy")
