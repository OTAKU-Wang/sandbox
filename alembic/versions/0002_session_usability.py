"""Session usability (Round 39) — additive columns for files/snapshots/pause.

Revision ID: 0002_session_usability
Revises: 0001_p0_security_hardening
Create Date: 2026-09-06

Additive-only, safe against a create_all database:

- sandbox_sessions.extended_seconds  (POST /{id}/refreshes — extends the
  effective expiry beyond timeout_seconds; is_session_expired adds it)
- sandbox_sessions.pre_pause_status  (pause stores the status to restore on
  resume; SUSPENDED transitions already exist in the state machine)
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0002_session_usability"
down_revision = "0001_p0_security_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sandbox_sessions",
        sa.Column("extended_seconds", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "sandbox_sessions",
        sa.Column("pre_pause_status", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sandbox_sessions", "pre_pause_status")
    op.drop_column("sandbox_sessions", "extended_seconds")
