"""Persist resumable long-running corpus synchronization jobs.

Revision ID: c12e94a8d6f1
Revises: b74c2e9f18d3
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c12e94a8d6f1"
down_revision: str | Sequence[str] | None = "b74c2e9f18d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "corpus_sync_jobs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("since_year", sa.Integer(), nullable=False),
        sa.Column("through_year", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(length=100), nullable=True),
        sa.Column("discovered", sa.Integer(), nullable=False),
        sa.Column("processed", sa.Integer(), nullable=False),
        sa.Column("succeeded", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("sentence_pairs", sa.Integer(), nullable=False),
        sa.Column("current_title", sa.String(length=512), nullable=True),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_corpus_sync_jobs_source"), "corpus_sync_jobs", ["source"]
    )
    op.create_index(
        op.f("ix_corpus_sync_jobs_status"), "corpus_sync_jobs", ["status"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_corpus_sync_jobs_status"), table_name="corpus_sync_jobs")
    op.drop_index(op.f("ix_corpus_sync_jobs_source"), table_name="corpus_sync_jobs")
    op.drop_table("corpus_sync_jobs")
