"""SS-14 e2e (D-e2e-08) — trained_models.product_id index missing from chain.

Revision ID: 0018_owner_fks
Revises: 0017_api_keys
Create Date: 2026-10-13

Additive-only (found by `alembic check` during the SS-14 remote e2e, where the
app boots on real PostgreSQL and the production deploy path is the migration
chain): the ORM carried index=True on trained_models.product_id that the chain
never created. SQLite (CI/dev) does not enforce the index, so the regression
suite stayed green while the PG path drifted.

The two ORM owner_id FKs (rag_generative_models, trained_models) that the same
check flagged were aligned to the chain on the ORM side instead: this repo's
chain must stay runnable on SQLite (CI gate), and SQLite cannot ALTER-ADD a
foreign key — the FK was never enforceable on the production (chain-built) PG
schema, so the ORM is the side that moves.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0018_owner_fks"
down_revision = "0017_api_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_trained_models_product_id", "trained_models", ["product_id"])


def downgrade() -> None:
    op.drop_index("ix_trained_models_product_id", table_name="trained_models")
