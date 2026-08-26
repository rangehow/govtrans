"""Terminology service (§13): lookup/search/create/update/deprecate with
audit logging. Translation Memory lexical+metadata search (§12; semantic
ranking activates with pgvector in E06/E08).
"""

from __future__ import annotations

from sqlalchemy import select

from apps.api.db import SessionLocal
from services.languages import resolve_language_pair
from services.terminology.models import Term, TermAuditLog


def term_lookup(
    source_terms: list[str],
    domain: str | None = None,
    *,
    source_language: str = "zh",
    target_language: str = "en",
) -> dict[str, dict]:
    """Batch exact lookup used by the terminology stage."""
    if not source_terms:
        return {}
    source_language, target_language = resolve_language_pair(source_language, target_language)
    with SessionLocal() as session:
        stmt = select(Term).where(
            Term.source_term.in_(source_terms),
            Term.status != "deprecated",
            Term.source_language == source_language,
            Term.target_language == target_language,
        )
        rows = session.execute(stmt).scalars().all()
        return {
            r.source_term: {
                "target": r.preferred_target,
                "domain": r.domain,
                "status": r.status,
                "origin": "term_db",
                "source_language": r.source_language,
                "target_language": r.target_language,
            }
            for r in rows
            if domain is None or r.domain in (None, domain)
        }


def terms_in_text(
    source_text: str,
    domain: str | None = None,
    limit: int = 200,
    *,
    source_language: str = "zh",
    target_language: str = "en",
) -> dict[str, dict]:
    """Return active, human-managed terms that actually occur in a document.

    This complements model-based term extraction: a binding glossary entry
    cannot disappear merely because the extractor omitted it.
    """
    if not source_text:
        return {}
    source_language, target_language = resolve_language_pair(source_language, target_language)
    with SessionLocal() as session:
        rows = (
            session.execute(
                select(Term).where(
                    Term.status != "deprecated",
                    Term.source_language == source_language,
                    Term.target_language == target_language,
                )
            )
            .scalars()
            .all()
        )
    matches = [
        row
        for row in rows
        if row.source_term in source_text and (domain is None or row.domain in (None, domain))
    ]
    matches.sort(key=lambda row: (-len(row.source_term), row.source_term))
    return {
        row.source_term: {
            "target": row.preferred_target,
            "domain": row.domain,
            "status": row.status,
            "origin": "term_db",
            "source_language": row.source_language,
            "target_language": row.target_language,
        }
        for row in matches[:limit]
    }


def term_search(
    query: str,
    top_k: int = 10,
    *,
    source_language: str | None = None,
    target_language: str | None = None,
) -> list[dict]:
    with SessionLocal() as session:
        stmt = select(Term).order_by(Term.updated_at.desc(), Term.source_term)
        if source_language is not None or target_language is not None:
            source_language, target_language = resolve_language_pair(
                source_language, target_language
            )
            stmt = stmt.where(
                Term.source_language == source_language,
                Term.target_language == target_language,
            )
        if query.strip():
            stmt = stmt.where(Term.source_term.contains(query.strip()))
        stmt = stmt.limit(top_k)
        rows = session.execute(stmt).scalars().all()
        return [
            {
                "id": r.id,
                "source_term": r.source_term,
                "preferred_target": r.preferred_target,
                "source_language": r.source_language,
                "target_language": r.target_language,
                "domain": r.domain,
                "status": r.status,
            }
            for r in rows
        ]


def term_create(
    source_term: str,
    preferred_target: str,
    *,
    source_language: str = "zh",
    target_language: str = "en",
    domain: str | None = None,
    context: str | None = None,
    actor: str = "system",
) -> str:
    source_language, target_language = resolve_language_pair(source_language, target_language)
    with SessionLocal() as session:
        term = Term(
            source_term=source_term,
            preferred_target=preferred_target,
            source_language=source_language,
            target_language=target_language,
            domain=domain,
            context=context,
        )
        session.add(term)
        session.flush()
        session.add(
            TermAuditLog(
                term_id=term.id,
                action="create",
                after={
                    "source_term": source_term,
                    "preferred_target": preferred_target,
                    "source_language": source_language,
                    "target_language": target_language,
                },
                actor=actor,
            )
        )
        session.commit()
        return term.id


def term_update(
    term_id: str,
    *,
    preferred_target: str | None = None,
    domain: str | None = None,
    context: str | None = None,
    actor: str = "system",
) -> bool:
    """Update mutable fields; every change goes to the audit log (§35)."""
    with SessionLocal() as session:
        term = session.get(Term, term_id)
        if not term:
            return False
        before = {
            "preferred_target": term.preferred_target,
            "domain": term.domain,
            "context": term.context,
        }
        if preferred_target is not None:
            term.preferred_target = preferred_target
        if domain is not None:
            term.domain = domain
        if context is not None:
            term.context = context
        after = {
            "preferred_target": term.preferred_target,
            "domain": term.domain,
            "context": term.context,
        }
        session.add(
            TermAuditLog(term_id=term.id, action="update", before=before, after=after, actor=actor)
        )
        session.commit()
        return True


def term_deprecate(term_id: str, *, actor: str = "system") -> bool:
    with SessionLocal() as session:
        term = session.get(Term, term_id)
        if not term:
            return False
        before = {"status": term.status}
        term.status = "deprecated"
        session.add(
            TermAuditLog(
                term_id=term.id,
                action="deprecate",
                before=before,
                after={"status": "deprecated"},
                actor=actor,
            )
        )
        session.commit()
        return True


# tm_search moved to services.retrieval.tm (BM25 + authority rerank, E06/E08).
from services.retrieval.tm import tm_search  # noqa: E402,F401  (back-compat re-export)
