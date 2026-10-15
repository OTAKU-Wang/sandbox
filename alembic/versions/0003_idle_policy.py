"""W9 — idle auto-pause policy columns on sandbox_sessions.

Revision ID: 0003_idle_policy
Revises: 0002_session_usability
Create Date: 2026-09-07

NOTE (SS-14 e2e D-e2e-08): auto_resume server_default text("0") → text("false") —
PostgreSQL rejects an integer default on a boolean column, so on PG this
migration could never have applied (no deployed PG schema carries the old
literal); SQLite tolerated 0, which is why it survived. See 0005's note.

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
        sa.Column("auto_resume", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("sandbox_sessions", "auto_resume")
    op.drop_column("sandbox_sessions", "idle_policy")
