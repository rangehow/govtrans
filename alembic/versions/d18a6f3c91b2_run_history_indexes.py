"""run history ordering and monotonic event invariant

Revision ID: d18a6f3c91b2
Revises: c7d635216dda
Create Date: 2026-08-24
"""
from collections.abc import Sequence

from alembic import op


revision: str = "d18a6f3c91b2"
down_revision: str | Sequence[str] | None = "c7d635216dda"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("translation_runs", schema=None) as batch_op:
        batch_op.create_index("ix_translation_runs_updated_at", ["updated_at"], unique=False)
    with op.batch_alter_table("run_events", schema=None) as batch_op:
        batch_op.create_unique_constraint("uq_run_events_run_seq", ["run_id", "seq"])


def downgrade() -> None:
    with op.batch_alter_table("run_events", schema=None) as batch_op:
        batch_op.drop_constraint("uq_run_events_run_seq", type_="unique")
    with op.batch_alter_table("translation_runs", schema=None) as batch_op:
        batch_op.drop_index("ix_translation_runs_updated_at")
