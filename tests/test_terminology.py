import pytest

from apps.api.db import SessionLocal
from services.terminology import service as ts
from services.terminology.models import TermAuditLog

pytestmark = pytest.mark.unit


class TestTerminologyLifecycle:
    def test_create_lookup_update_deprecate_with_audit(self):
        term_id = ts.term_create(
            "新质生产力", "new quality productive forces", domain="economy", actor="tester"
        )
        # lookup
        hits = ts.term_lookup(["新质生产力"])
        assert hits["新质生产力"]["target"] == "new quality productive forces"
        # update
        assert ts.term_update(
            term_id, preferred_target="new-quality productive forces", actor="tester"
        )
        hits = ts.term_lookup(["新质生产力"])
        assert hits["新质生产力"]["target"] == "new-quality productive forces"
        # search
        assert ts.term_search("新质")[0]["id"] == term_id
        # deprecate -> excluded from lookup
        assert ts.term_deprecate(term_id, actor="tester")
        assert ts.term_lookup(["新质生产力"]) == {}
        # audit trail complete
        with SessionLocal() as session:
            actions = [
                r.action
                for r in session.query(TermAuditLog)
                .filter_by(term_id=term_id)
                .order_by(TermAuditLog.id)
                .all()
            ]
        assert actions == ["create", "update", "deprecate"]

    def test_update_missing_term(self):
        assert not ts.term_update("nonexistent", preferred_target="x")
        assert not ts.term_deprecate("nonexistent")

    def test_same_source_term_is_isolated_by_language_pair(self):
        ts.term_create(
            "réforme-pair-test",
            "Reform",
            source_language="fr",
            target_language="en",
        )
        ts.term_create(
            "réforme-pair-test",
            "Reformpolitik",
            source_language="fr",
            target_language="de",
        )
        assert (
            ts.term_lookup(
                ["réforme-pair-test"],
                source_language="fr",
                target_language="en",
            )["réforme-pair-test"]["target"]
            == "Reform"
        )
        assert (
            ts.term_lookup(
                ["réforme-pair-test"],
                source_language="fr",
                target_language="de",
            )["réforme-pair-test"]["target"]
            == "Reformpolitik"
        )


class TestTMSearch:
    def test_lexical_ranking_and_filters(self):
        from services.retrieval.models import TMEntry
        from services.retrieval.tm import invalidate_reference_indexes

        with SessionLocal() as session:
            session.add(
                TMEntry(
                    source="推动高质量发展",
                    target="promote high-quality development",
                    document_type="white_paper",
                    domain="economy",
                    authority="official_verified",
                )
            )
            session.add(
                TMEntry(
                    source=" unrelated content here", target="unrelated", document_type="speech"
                )
            )
            session.commit()
        invalidate_reference_indexes()
        hits = ts.tm_search("高质量发展是首要任务", document_type="white_paper")
        assert hits and hits[0]["authority"] == "official_verified"
        assert hits[0]["target"] == "promote high-quality development"
        # metadata filter excludes the speech entry entirely
        assert all(h["target"] != "unrelated" for h in hits)

    def test_verified_memory_can_be_used_in_the_reverse_direction(self):
        from services.retrieval.models import TMEntry
        from services.retrieval.tm import invalidate_reference_indexes

        with SessionLocal() as session:
            session.add(
                TMEntry(
                    source="coopération multilatérale unique",
                    target="einzigartige multilaterale Zusammenarbeit",
                    source_language="fr",
                    target_language="de",
                    authority="official_verified",
                )
            )
            session.commit()
        invalidate_reference_indexes()
        hits = ts.tm_search(
            "einzigartige multilaterale Zusammenarbeit",
            source_language="de",
            target_language="fr",
        )
        assert hits[0]["target"] == "coopération multilatérale unique"
        assert hits[0]["source_language"] == "de"

    def test_high_confidence_official_alignment_is_an_automatic_soft_reference(self):
        import uuid

        from services.corpus.models import AlignedPair, CorpusDocument, DocumentPair
        from services.retrieval.tm import invalidate_reference_indexes

        with SessionLocal() as session:
            zh_doc = CorpusDocument(
                lang="zh",
                title="自动参考测试白皮书",
                raw_text="持续推进海洋生态文明建设。",
                structure=[],
                content_hash=uuid.uuid4().hex,
                document_type="reference_test",
                domain="marine",
                url="http://www.scio.gov.cn/zfbps/reference-test.html",
            )
            en_doc = CorpusDocument(
                lang="en",
                raw_text="China will continue to advance marine eco-environmental progress.",
                structure=[],
                content_hash=uuid.uuid4().hex,
                document_type="reference_test",
                domain="marine",
                url="http://english.scio.gov.cn/whitepapers/reference-test.html",
            )
            session.add_all([zh_doc, en_doc])
            session.flush()
            doc_pair = DocumentPair(zh_doc_id=zh_doc.id, en_doc_id=en_doc.id)
            session.add(doc_pair)
            session.flush()
            session.add_all(
                [
                    AlignedPair(
                        pair_id=doc_pair.id,
                        level="sentence",
                        idx="0.0",
                        zh_text="持续推进海洋生态文明建设。",
                        en_text=(
                            "China will continue to advance marine eco-environmental progress."
                        ),
                        score=0.93,
                    ),
                    AlignedPair(
                        pair_id=doc_pair.id,
                        level="sentence",
                        idx="0.1",
                        zh_text="低置信独有样本不应启用。",
                        en_text="This low-confidence sample must stay inactive.",
                        score=0.5,
                    ),
                ]
            )
            session.commit()

        invalidate_reference_indexes()
        hits = ts.tm_search(
            "要继续推进海洋生态文明建设",
            document_type="reference_test",
        )
        assert hits
        assert hits[0]["kind"] == "official_corpus"
        assert hits[0]["usage"] == "advisory"
        assert hits[0]["alignment_score"] == pytest.approx(0.93)
        assert all("低置信独有样本" not in hit["source"] for hit in hits)
