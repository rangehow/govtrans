"""make translation runs, terminology and TM language-pair aware

Revision ID: f6c2a9d41b70
Revises: e5b91a72fc40
Create Date: 2026-08-25 18:20:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "f6c2a9d41b70"
down_revision: str | Sequence[str] | None = "e5b91a72fc40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("translation_runs") as batch_op:
        batch_op.alter_column(
            "direction",
            existing_type=sa.String(length=8),
            type_=sa.String(length=35),
            existing_nullable=False,
        )
        batch_op.add_column(
            sa.Column(
                "source_language",
                sa.String(length=16),
                nullable=False,
                server_default="zh",
            )
        )
        batch_op.add_column(
            sa.Column(
                "target_language",
                sa.String(length=16),
                nullable=False,
                server_default="en",
            )
        )
        batch_op.create_index(
            "ix_translation_runs_language_pair",
            ["source_language", "target_language"],
        )
    # The only non-default direction accepted by the legacy API was en-zh.
    # Preserve that meaning instead of treating every historical row as zh-en.
    op.execute(
        "UPDATE translation_runs SET source_language = 'en', "
        "target_language = 'zh' WHERE direction = 'en-zh'"
    )

    with op.batch_alter_table("terms") as batch_op:
        batch_op.add_column(
            sa.Column(
                "source_language",
                sa.String(length=16),
                nullable=False,
                server_default="zh",
            )
        )
        batch_op.add_column(
            sa.Column(
                "target_language",
                sa.String(length=16),
                nullable=False,
                server_default="en",
            )
        )
        batch_op.create_index("ix_terms_language_pair", ["source_language", "target_language"])

    with op.batch_alter_table("translation_memory") as batch_op:
        batch_op.add_column(
            sa.Column(
                "source_language",
                sa.String(length=16),
                nullable=False,
                server_default="zh",
            )
        )
        batch_op.add_column(
            sa.Column(
                "target_language",
                sa.String(length=16),
                nullable=False,
                server_default="en",
            )
        )
        batch_op.create_index(
            "ix_translation_memory_language_pair",
            ["source_language", "target_language"],
        )

    with op.batch_alter_table("corpus_documents") as batch_op:
        batch_op.alter_column(
            "lang",
            existing_type=sa.String(length=8),
            type_=sa.String(length=16),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("corpus_documents") as batch_op:
        batch_op.alter_column(
            "lang",
            existing_type=sa.String(length=16),
            type_=sa.String(length=8),
            existing_nullable=False,
        )

    with op.batch_alter_table("translation_memory") as batch_op:
        batch_op.drop_index("ix_translation_memory_language_pair")
        batch_op.drop_column("target_language")
        batch_op.drop_column("source_language")

    with op.batch_alter_table("terms") as batch_op:
        batch_op.drop_index("ix_terms_language_pair")
        batch_op.drop_column("target_language")
        batch_op.drop_column("source_language")

    with op.batch_alter_table("translation_runs") as batch_op:
        batch_op.drop_index("ix_translation_runs_language_pair")
        batch_op.drop_column("target_language")
        batch_op.drop_column("source_language")
        batch_op.alter_column(
            "direction",
            existing_type=sa.String(length=35),
            type_=sa.String(length=8),
            existing_nullable=False,
        )
