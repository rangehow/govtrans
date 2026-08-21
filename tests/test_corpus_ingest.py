import pytest

from apps.api.db import SessionLocal
from services.corpus.ingest import ingest_document_pair
from services.corpus.models import AlignedPair, CorpusDocument
from services.retrieval.models import TMEntry

pytestmark = pytest.mark.unit

ZH_HTML = """
<html><head><title>测试白皮书</title></head><body>
<p>来源：测试社 2024年5月1日</p>
<p>中国坚持绿色发展，能源转型取得历史性成就。</p>
<p>2023年清洁能源消费比重达到26.4%。</p>
</body></html>
"""

EN_HTML = """
<html><head><title>Test White Paper</title></head><body>
<p>Source: Test Agency, May 1, 2024</p>
<p>China pursues green development and has achieved historic progress in its energy transition.</p>
<p>In 2023, the share of clean energy consumption reached 26.4%.</p>
</body></html>
"""


@pytest.fixture
def ingest_once():
    return ingest_document_pair(
        zh_source=ZH_HTML, en_source=EN_HTML, is_html=True,
        zh_url="http://www.scio.gov.cn/test/zh.htm", en_url="http://english.scio.gov.cn/test/en.htm",
        document_type="white_paper", domain="energy",
    )


class TestIngestPipeline:
    def test_documents_persisted_with_provenance(self, ingest_once):
        with SessionLocal() as session:
            zh = session.get(CorpusDocument, ingest_once.zh_doc_id)
            assert zh.raw_html and "绿色发展" in zh.raw_html  # raw kept
            assert zh.doc_metadata.get("publish_date") == "2024-05-01"
            assert zh.content_hash

    def test_alignment_counts(self, ingest_once):
        # fixture paragraphs are all single-sentence -> sentence-level rows only
        assert ingest_once.paragraph_pairs == 0
        assert ingest_once.sentence_pairs >= 2

    def test_sentence_pairs_scored(self, ingest_once):
        with SessionLocal() as session:
            rows = session.query(AlignedPair).filter_by(
                pair_id=ingest_once.pair_id, level="sentence").all()
            assert rows
            by_text = [r for r in rows if "26.4%" in r.zh_text]
            assert by_text and by_text[0].score >= 0.5
            assert all(r.provenance.get("zh_url") for r in rows)

    def test_tm_promotion_with_authority(self, ingest_once):
        with SessionLocal() as session:
            tms = session.query(TMEntry).filter_by(document_type="white_paper").all()
            assert tms
            assert all(t.authority == "official_aligned" for t in tms)
            assert all(t.provenance.get("aligned_pair_id") for t in tms)

    def test_reingest_is_idempotent_on_documents(self, ingest_once):
        again = ingest_document_pair(
            zh_source=ZH_HTML, en_source=EN_HTML, is_html=True, promote=False)
        assert again.zh_doc_id == ingest_once.zh_doc_id  # content-deduped
        assert again.en_doc_id == ingest_once.en_doc_id


class TestCrawlFailure:
    def test_fetch_failure_is_actionable(self, monkeypatch):
        import services.corpus.crawler as crawler

        def boom(url, **kwargs):
            raise ConnectionError("no route")
        monkeypatch.setattr("tofu_search.fetch_url", boom)
        with pytest.raises(crawler.CrawlError, match="no route"):
            crawler.fetch_document("http://example.invalid/x")
