"""Record whether a runtime style rule was activated automatically or manually.

Revision ID: d4f0c2a91e73
Revises: c12e94a8d6f1
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4f0c2a91e73"
down_revision: str | Sequence[str] | None = "c12e94a8d6f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "style_rules",
        sa.Column("activation_source", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "style_rules",
        sa.Column("activated_at", sa.DateTime(), nullable=True),
    )
    op.execute(
        "UPDATE style_rules "
        "SET activation_source = 'automatic', activated_at = created_at "
        "WHERE status = 'approved' AND activation_source IS NULL"
    )


def downgrade() -> None:
    op.drop_column("style_rules", "activated_at")
    op.drop_column("style_rules", "activation_source")
