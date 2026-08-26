"""separate style skills, manual terms, and translation strategy

Revision ID: e31b7c8a4d20
Revises: d18a6f3c91b2
Create Date: 2026-08-24 15:20:00
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op


revision: str = "e31b7c8a4d20"
down_revision: str | Sequence[str] | None = "d18a6f3c91b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_OFFICIAL_TERMS = [
    ("高质量发展", "high-quality development"),
    ("高水平对外开放", "high-standard opening up"),
    ("市场化、法治化、国际化一流营商环境", "world-class business environment that is market-oriented, law-based, and internationalized"),
    ("国内生产总值", "gross domestic product (GDP)"),
    ("新发展格局", "the new development pattern"),
    ("供给侧结构性改革", "supply-side structural reform"),
    ("共同富裕", "common prosperity"),
    ("一带一路", "the Belt and Road Initiative (BRI)"),
    ("中国特色社会主义", "socialism with Chinese characteristics"),
    ("全过程人民民主", "whole-process people's democracy"),
    ("国务院", "the State Council"),
    ("全国人民代表大会", "the National People's Congress (NPC)"),
    ("中国人民政治协商会议", "the Chinese People's Political Consultative Conference (CPPCC)"),
]


def _term_id(source: str) -> str:
    return hashlib.sha256(f"govtrans-official:{source}".encode()).hexdigest()[:32]


def upgrade() -> None:
    with op.batch_alter_table("translation_runs") as batch_op:
        batch_op.add_column(sa.Column(
            "style_skills", sa.JSON(), nullable=False, server_default=sa.text("'[]'"),
        ))
        batch_op.add_column(sa.Column(
            "manual_terms", sa.JSON(), nullable=False, server_default=sa.text("'[]'"),
        ))
        batch_op.add_column(sa.Column(
            "translation_mode", sa.String(length=16), nullable=False,
            server_default="coherent",
        ))

    connection = op.get_bind()
    existing = set(connection.execute(sa.text("SELECT source_term FROM terms")).scalars())
    terms = sa.table(
        "terms",
        sa.column("id", sa.String),
        sa.column("source_term", sa.String),
        sa.column("preferred_target", sa.String),
        sa.column("domain", sa.String),
        sa.column("context", sa.Text),
        sa.column("status", sa.String),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    now = datetime.now(timezone.utc)
    rows = [
        {
            "id": _term_id(source),
            "source_term": source,
            "preferred_target": target,
            "domain": None,
            "context": "系统初始官方术语；可在术语库中人工编辑或弃用",
            "status": "preferred",
            "created_at": now,
            "updated_at": now,
        }
        for source, target in _OFFICIAL_TERMS
        if source not in existing
    ]
    if rows:
        op.bulk_insert(terms, rows)


def downgrade() -> None:
    ids = [_term_id(source) for source, _target in _OFFICIAL_TERMS]
    op.get_bind().execute(
        sa.text("DELETE FROM terms WHERE id IN :ids").bindparams(
            sa.bindparam("ids", expanding=True)
        ),
        {"ids": ids},
    )
    with op.batch_alter_table("translation_runs") as batch_op:
        batch_op.drop_column("translation_mode")
        batch_op.drop_column("manual_terms")
        batch_op.drop_column("style_skills")
