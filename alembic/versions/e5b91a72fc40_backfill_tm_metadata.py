"""Backfill corpus metadata on legacy human-verified reference rows.

Revision ID: e5b91a72fc40
Revises: d4f0c2a91e73
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e5b91a72fc40"
down_revision: str | Sequence[str] | None = "d4f0c2a91e73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _corpus_value(column: str) -> str:
    return (
        f"(SELECT corpus_documents.{column} "
        "FROM aligned_pairs "
        "JOIN document_pairs ON document_pairs.id = aligned_pairs.pair_id "
        "JOIN corpus_documents "
        "ON corpus_documents.id = document_pairs.zh_doc_id "
        "WHERE aligned_pairs.tm_entry_id = translation_memory.id LIMIT 1)"
    )


def upgrade() -> None:
    for column in ("document_type", "domain", "url"):
        value = _corpus_value(column)
        op.execute(
            f"UPDATE translation_memory SET {column} = {value} "
            f"WHERE {column} IS NULL AND {value} IS NOT NULL"
        )
    title = _corpus_value("title")
    url = _corpus_value("url")
    op.execute(
        "UPDATE translation_memory "
        f"SET source_document = COALESCE({title}, {url}) "
        "WHERE source_document IS NULL "
        f"AND COALESCE({title}, {url}) IS NOT NULL"
    )


def downgrade() -> None:
    # Metadata may have been edited after the upgrade and must not be erased.
    pass
