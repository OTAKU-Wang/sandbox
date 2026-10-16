"""SS-13 — sandbox_sessions TEE policy + trust root (single-path cutover).

Additive-only: sandbox_session gains
  - tee_policy  (tee_if_available | tee_required | none), default
    "tee_if_available" — the TEE selection policy requested at create time;
  - trust_root  (tdx | sev_snp | none), default "none" — the per-session
    trust root of the selected cube node (honest degradation: absent/malformed
    node TEE capability is stored as "none", never upgraded).

Revision ID: 0016_sandbox_tee_policy
Revises: 0015_cube_egress_dedup
Create Date: 2026-10-13
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0016_sandbox_tee_policy"
down_revision = "0015_cube_egress_dedup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sandbox_sessions",
        sa.Column("tee_policy", sa.String(length=32), nullable=True, server_default="tee_if_available"),
    )
    op.add_column(
        "sandbox_sessions",
        sa.Column("trust_root", sa.String(length=16), nullable=True, server_default="none"),
    )


def downgrade() -> None:
    op.drop_column("sandbox_sessions", "trust_root")
    op.drop_column("sandbox_sessions", "tee_policy")
