"""Terminology service (§13): lookup/search/create/update/deprecate with
audit logging. Translation Memory lexical+metadata search (§12; semantic
ranking activates with pgvector in E06/E08).
"""
from __future__ import annotations

from sqlalchemy import select

from apps.api.db import SessionLocal
from services.retrieval.models import TMEntry
from services.terminology.models import Term, TermAuditLog


def term_lookup(source_terms: list[str], domain: str | None = None) -> dict[str, dict]:
    """Batch exact lookup used by the terminology stage."""
    if not source_terms:
        return {}
    with SessionLocal() as session:
        stmt = select(Term).where(Term.source_term.in_(source_terms), Term.status != "deprecated")
        rows = session.execute(stmt).scalars().all()
        return {
            r.source_term: {
                "target": r.preferred_target,
                "domain": r.domain,
                "status": r.status,
                "origin": "term_db",
            }
            for r in rows
            if domain is None or r.domain in (None, domain)
        }


def term_search(query: str, top_k: int = 10) -> list[dict]:
    with SessionLocal() as session:
        stmt = select(Term).where(Term.source_term.contains(query)).limit(top_k)
        rows = session.execute(stmt).scalars().all()
        return [
            {"id": r.id, "source_term": r.source_term, "preferred_target": r.preferred_target,
             "domain": r.domain, "status": r.status}
            for r in rows
        ]


def term_create(source_term: str, preferred_target: str, *, domain: str | None = None,
                context: str | None = None, actor: str = "system") -> str:
    with SessionLocal() as session:
        term = Term(source_term=source_term, preferred_target=preferred_target,
                    domain=domain, context=context)
        session.add(term)
        session.flush()
        session.add(TermAuditLog(term_id=term.id, action="create",
                                 after={"source_term": source_term, "preferred_target": preferred_target},
                                 actor=actor))
        session.commit()
        return term.id


def term_update(term_id: str, *, preferred_target: str | None = None,
                domain: str | None = None, context: str | None = None,
                actor: str = "system") -> bool:
    """Update mutable fields; every change goes to the audit log (§35)."""
    with SessionLocal() as session:
        term = session.get(Term, term_id)
        if not term:
            return False
        before = {"preferred_target": term.preferred_target,
                  "domain": term.domain, "context": term.context}
        if preferred_target is not None:
            term.preferred_target = preferred_target
        if domain is not None:
            term.domain = domain
        if context is not None:
            term.context = context
        after = {"preferred_target": term.preferred_target,
                 "domain": term.domain, "context": term.context}
        session.add(TermAuditLog(term_id=term.id, action="update",
                                 before=before, after=after, actor=actor))
        session.commit()
        return True


def term_deprecate(term_id: str, *, actor: str = "system") -> bool:
    with SessionLocal() as session:
        term = session.get(Term, term_id)
        if not term:
            return False
        before = {"status": term.status}
        term.status = "deprecated"
        session.add(TermAuditLog(term_id=term.id, action="deprecate", before=before,
                                 after={"status": "deprecated"}, actor=actor))
        session.commit()
        return True


def tm_search(text: str, *, document_type: str | None = None, domain: str | None = None,
              top_k: int = 5) -> list[dict]:
    """Lexical + metadata TM search. Returns evidence dicts with authority.
    Semantic ranking plugs in here once embeddings are populated (E06)."""
    with SessionLocal() as session:
        stmt = select(TMEntry)
        if document_type:
            stmt = stmt.where(TMEntry.document_type == document_type)
        if domain:
            stmt = stmt.where(TMEntry.domain == domain)
        rows = session.execute(stmt.limit(500)).scalars().all()
    # Overlap scoring in Python: cheap, deterministic, dialect-portable.
    query_terms = set(text)
    scored = []
    for row in rows:
        overlap = sum(1 for ch in set(row.source) if ch in query_terms)
        score = overlap / max(len(set(row.source)), 1)
        if score > 0.15:
            scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "source": r.source, "target": r.target, "score": round(score, 3),
            "source_document": r.source_document, "url": r.url, "authority": r.authority,
        }
        for score, r in scored[:top_k]
    ]
