"""Version bilingual alignment output for safe automatic rebuilding.

Revision ID: b74c2e9f18d3
Revises: a63f104de9b7
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b74c2e9f18d3"
down_revision: str | Sequence[str] | None = "a63f104de9b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_pairs",
        sa.Column(
            "alignment_version",
            sa.String(length=16),
            nullable=False,
            server_default="1",
        ),
    )


def downgrade() -> None:
    op.drop_column("document_pairs", "alignment_version")
