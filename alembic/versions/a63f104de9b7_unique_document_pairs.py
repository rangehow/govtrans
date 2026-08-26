"""Deduplicate and uniquely identify bilingual document pairs.

Revision ID: a63f104de9b7
Revises: f42d9a17c6e1
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a63f104de9b7"
down_revision: str | Sequence[str] | None = "f42d9a17c6e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text(
        "SELECT id, zh_doc_id, en_doc_id FROM document_pairs "
        "ORDER BY created_at, id"
    )).mappings().all()
    seen: set[tuple[str, str]] = set()
    duplicates: list[str] = []
    for row in rows:
        key = (row["zh_doc_id"], row["en_doc_id"])
        if key in seen:
            duplicates.append(row["id"])
        else:
            seen.add(key)
    for pair_id in duplicates:
        connection.execute(
            sa.text("DELETE FROM aligned_pairs WHERE pair_id = :pair_id"),
            {"pair_id": pair_id},
        )
        connection.execute(
            sa.text("DELETE FROM document_pairs WHERE id = :pair_id"),
            {"pair_id": pair_id},
        )
    op.create_index(
        "uq_document_pairs_zh_en",
        "document_pairs",
        ["zh_doc_id", "en_doc_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_document_pairs_zh_en", table_name="document_pairs")
