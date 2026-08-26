"""normalize segment status for already completed runs

Revision ID: f42d9a17c6e1
Revises: e31b7c8a4d20
Create Date: 2026-08-24 15:40:00
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f42d9a17c6e1"
down_revision: str | Sequence[str] | None = "e31b7c8a4d20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        UPDATE segments
        SET status = 'final'
        WHERE translation IS NOT NULL
          AND run_id IN (
            SELECT id FROM translation_runs WHERE status = 'COMPLETED'
          )
    """)


def downgrade() -> None:
    # Historical rows cannot be distinguished reliably; status normalization
    # is intentionally data-safe and therefore not reversed.
    pass
