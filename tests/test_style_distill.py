import pytest

from apps.api.db import SessionLocal
from pipelines.style_distillation.mine import mine_candidate_rules
from pipelines.style_distillation.models import StyleRule
from services.corpus.models import AlignedPair, DocumentPair, CorpusDocument

pytestmark = pytest.mark.unit


@pytest.fixture
def seeded_pairs():
    import uuid

    # shared session DB: isolation via table cleanup (support counts are
    # cumulative by design in the miner, so tests must start empty)
    with SessionLocal() as session:
        for table in (StyleRule, AlignedPair, DocumentPair, CorpusDocument):
            session.query(table).delete()
        session.commit()
    with SessionLocal() as session:
        zh_doc = CorpusDocument(lang="zh", raw_text="t", structure=[],
                                content_hash=uuid.uuid4().hex, domain="economy")
        en_doc = CorpusDocument(lang="en", raw_text="t", structure=[],
                                content_hash=uuid.uuid4().hex)
        session.add_all([zh_doc, en_doc])
        session.flush()
        pair = DocumentPair(zh_doc_id=zh_doc.id, en_doc_id=en_doc.id)
        session.add(pair)
        session.flush()
        rows = [
            # 坚持... -> uphold twice, adhere once
            ("坚持绿色发展理念。", "Uphold the philosophy of green development.", 0.8),
            ("坚持节约优先。", "Uphold the principle of conservation first.", 0.8),
            ("坚持人民至上。", "Adhere to the people-first principle.", 0.8),
            # 加快... -> accelerate twice
            ("加快构建新发展格局。", "Accelerate the creation of a new development pattern.", 0.8),
            ("加快推进生态文明。", "Accelerate ecological progress.", 0.8),
            # low-score pair must be ignored by mining
            ("坚持低分对。", "whatever translation.", 0.3),
        ]
        for i, (zh, en, score) in enumerate(rows):
            session.add(AlignedPair(pair_id=pair.id, level="sentence", idx=f"0.{i}",
                                    zh_text=zh, en_text=en, score=score,
                                    provenance={"zh_doc": zh_doc.id}))
        session.commit()


class TestStyleMining:
    def test_mines_supported_rules(self, seeded_pairs):
        stats = mine_candidate_rules(min_support=2)
        assert stats["created"] >= 2
        with SessionLocal() as session:
            rules = session.query(StyleRule).all()
            by_family = {r.en_rendering: r for r in rules}
            assert "uphold" in by_family
            uphold = by_family["uphold"]
            assert uphold.source_count == 2
            assert uphold.confidence == pytest.approx(2 / 3, abs=0.01)
            assert uphold.domains == ["economy"]
            assert uphold.status == "candidate"
            assert len(uphold.examples) == 2
            assert "accelerate" in by_family

    def test_rerun_updates_not_duplicates(self, seeded_pairs):
        mine_candidate_rules(min_support=2)
        stats = mine_candidate_rules(min_support=2)
        assert stats["created"] == 0 and stats["updated"] >= 2
        with SessionLocal() as session:
            count = session.query(StyleRule).count()
        assert count == 2

    def test_low_score_pairs_ignored(self, seeded_pairs):
        mine_candidate_rules(min_support=2)
        with SessionLocal() as session:
            for rule in session.query(StyleRule).all():
                assert all("低分对" not in e["zh"] for e in rule.examples)
