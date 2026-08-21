import pytest

from apps.api.db import SessionLocal
from services.terminology import service as ts
from services.terminology.models import Term, TermAuditLog

pytestmark = pytest.mark.unit


class TestTerminologyLifecycle:
    def test_create_lookup_update_deprecate_with_audit(self):
        term_id = ts.term_create("新质生产力", "new quality productive forces",
                                 domain="economy", actor="tester")
        # lookup
        hits = ts.term_lookup(["新质生产力"])
        assert hits["新质生产力"]["target"] == "new quality productive forces"
        # update
        assert ts.term_update(term_id, preferred_target="new-quality productive forces",
                              actor="tester")
        hits = ts.term_lookup(["新质生产力"])
        assert hits["新质生产力"]["target"] == "new-quality productive forces"
        # search
        assert ts.term_search("新质")[0]["id"] == term_id
        # deprecate -> excluded from lookup
        assert ts.term_deprecate(term_id, actor="tester")
        assert ts.term_lookup(["新质生产力"]) == {}
        # audit trail complete
        with SessionLocal() as session:
            actions = [r.action for r in session.query(TermAuditLog)
                       .filter_by(term_id=term_id).order_by(TermAuditLog.id).all()]
        assert actions == ["create", "update", "deprecate"]

    def test_update_missing_term(self):
        assert not ts.term_update("nonexistent", preferred_target="x")
        assert not ts.term_deprecate("nonexistent")


class TestTMSearch:
    def test_lexical_ranking_and_filters(self):
        from services.retrieval.models import TMEntry

        with SessionLocal() as session:
            session.add(TMEntry(source="推动高质量发展", target="promote high-quality development",
                                document_type="white_paper", domain="economy",
                                authority="official_verified"))
            session.add(TMEntry(source=" unrelated content here",
                                target="unrelated", document_type="speech"))
            session.commit()
        hits = ts.tm_search("高质量发展是首要任务", document_type="white_paper")
        assert hits and hits[0]["authority"] == "official_verified"
        assert hits[0]["target"] == "promote high-quality development"
        # metadata filter excludes the speech entry entirely
        assert all(h["target"] != "unrelated" for h in hits)
