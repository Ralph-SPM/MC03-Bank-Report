"""Create the MC03 persistence and immutable-evidence foundation."""

from __future__ import annotations

from alembic import op

from mc03.persistence.schema import create_foundation_schema, drop_foundation_schema

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create foundation tables and append-only guards."""
    create_foundation_schema(op.get_bind())


def downgrade() -> None:
    """Drop foundation tables for isolated development/test databases."""
    drop_foundation_schema(op.get_bind())
