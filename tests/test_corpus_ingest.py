import asyncio

import pytest
from fastapi import HTTPException

from apps.api.db import SessionLocal
from apps.api.routes.corpus import (
    AlignmentReviewRequest,
    ScioImportRequest,
    _validate_scio_url,
    import_scio_pair,
    list_alignments,
    review_alignment,
)
from services.corpus.ingest import ingest_document_pair
from services.corpus.models import (
    CURRENT_ALIGNMENT_VERSION,
    AlignedPair,
    CorpusDocument,
    CorpusSyncJob,
    DocumentPair,
)
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
        document_type="white_paper", domain="energy", promote=True,
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
            tms = [
                row for row in session.query(TMEntry).filter_by(
                    document_type="white_paper"
                ).all()
                if row.provenance.get("pair_id") == ingest_once.pair_id
            ]
            assert tms
            assert all(t.authority == "official_aligned" for t in tms)
            assert all(t.provenance.get("aligned_pair_id") for t in tms)

    def test_reingest_is_idempotent_on_documents(self, ingest_once):
        with SessionLocal() as session:
            before_pairs = session.query(DocumentPair).count()
            before_aligned = session.query(AlignedPair).count()
        again = ingest_document_pair(
            zh_source=ZH_HTML, en_source=EN_HTML, is_html=True, promote=False)
        assert again.zh_doc_id == ingest_once.zh_doc_id  # content-deduped
        assert again.en_doc_id == ingest_once.en_doc_id
        assert again.pair_id == ingest_once.pair_id
        assert again.warnings
        with SessionLocal() as session:
            assert session.query(DocumentPair).count() == before_pairs
            assert session.query(AlignedPair).count() == before_aligned

    def test_high_score_reference_is_active_without_review_and_human_is_exceptional(self):
        import uuid

        with SessionLocal() as session:
            zh = CorpusDocument(
                lang="zh", raw_text="官方参考状态测试。", structure=[],
                title="官方参考状态测试",
                document_type="white_paper", domain="governance",
                url="http://www.scio.gov.cn/zfbps/reference-tier-test.html",
                content_hash=uuid.uuid4().hex,
            )
            en = CorpusDocument(
                lang="en", raw_text="Official reference-state test.", structure=[],
                title="Official reference-state test",
                document_type="white_paper", domain="governance",
                url="http://english.scio.gov.cn/whitepapers/reference-tier-test.html",
                content_hash=uuid.uuid4().hex,
            )
            session.add_all([zh, en])
            session.flush()
            doc_pair = DocumentPair(zh_doc_id=zh.id, en_doc_id=en.id)
            session.add(doc_pair)
            session.flush()
            aligned = AlignedPair(
                pair_id=doc_pair.id, level="sentence", idx="0.0",
                zh_text="官方参考状态测试。",
                en_text="Official reference-state test.", score=0.91,
            )
            session.add(aligned)
            session.commit()
            pair_id, alignment_id = doc_pair.id, aligned.id

        automatic = list_alignments(pair_id, level="sentence")["alignments"][0]
        assert automatic["reference_tier"] == "automatic"
        assert automatic["tm_entry_id"] is None

        reviewed = review_alignment(
            alignment_id, AlignmentReviewRequest(status="approved")
        )
        assert reviewed["tm_entry_id"]
        with SessionLocal() as session:
            tm = session.get(TMEntry, reviewed["tm_entry_id"])
            assert tm.document_type == "white_paper"
            assert tm.source_document == "官方参考状态测试"

        restored = review_alignment(
            alignment_id, AlignmentReviewRequest(status="auto")
        )
        assert restored["tm_entry_id"] is None
        assert list_alignments(pair_id, level="sentence")["alignments"][0][
            "reference_tier"
        ] == "automatic"

    def test_stale_alignment_rebuild_preserves_reviewed_rows(self):
        zh_html = (
            "<html><head><title>版本化对齐测试</title></head><body>"
            "<p>前言</p><p>目录占位内容。</p><p>前言</p>"
            "<p>中国持续推动绿色转型。</p><p>合作为世界带来新机遇。</p>"
            "</body></html>"
        )
        en_html = (
            "<html><head><title>Versioned Alignment Test</title></head><body>"
            "<p>Preface</p><p>Contents placeholder.</p><p>Preface</p>"
            "<p>China continues to advance its green transition.</p>"
            "<p>Cooperation brings new opportunities to the world.</p>"
            "</body></html>"
        )
        first = ingest_document_pair(
            zh_source=zh_html,
            en_source=en_html,
            is_html=True,
            zh_url="http://www.scio.gov.cn/zfbps/versioned-test.html",
            en_url="http://english.scio.gov.cn/whitepapers/versioned-test.html",
        )
        with SessionLocal() as session:
            pair = session.get(DocumentPair, first.pair_id)
            rows = session.query(AlignedPair).filter_by(pair_id=first.pair_id).all()
            assert len(rows) >= 2
            reviewed_id = rows[0].id
            old_auto_ids = {row.id for row in rows[1:]}
            rows[0].status = "approved"
            pair.alignment_version = "1"
            session.commit()

        rebuilt = ingest_document_pair(
            zh_source=zh_html,
            en_source=en_html,
            is_html=True,
            zh_url="http://www.scio.gov.cn/zfbps/versioned-test.html",
            en_url="http://english.scio.gov.cn/whitepapers/versioned-test.html",
        )
        assert not rebuilt.warnings
        with SessionLocal() as session:
            pair = session.get(DocumentPair, first.pair_id)
            rows = session.query(AlignedPair).filter_by(pair_id=first.pair_id).all()
            assert pair.alignment_version == CURRENT_ALIGNMENT_VERSION
            assert session.get(AlignedPair, reviewed_id) is not None
            assert old_auto_ids.isdisjoint({row.id for row in rows})


class TestCrawlFailure:
    def test_fetch_failure_is_actionable(self, monkeypatch):
        import services.corpus.crawler as crawler

        def boom(url, **kwargs):
            raise ConnectionError("no route")
        monkeypatch.setattr("tofu_search.fetch_url_bytes", boom)
        with pytest.raises(crawler.CrawlError, match="no route"):
            crawler.fetch_document("http://example.invalid/x")

    def test_fetch_preserves_raw_html_on_official_english_fast_path(self, monkeypatch):
        import services.corpus.crawler as crawler

        calls = []
        raw = (
            "<html><head><meta charset='utf-8'><title>White Paper</title></head>"
            "<body><p>" + "Official paragraph evidence. " * 12 + "</p></body></html>"
        )

        def fake_fetch(url, **kwargs):
            calls.append((url, kwargs))
            return raw

        monkeypatch.setattr(crawler, "_fetch_official_english_raw", fake_fetch)
        result = crawler.fetch_document(
            "https://english.scio.gov.cn/whitepapers/2024/content_123.htm"
        )
        assert "<p>" in result and "Official paragraph evidence" in result
        assert calls == [(
            "https://english.scio.gov.cn/whitepapers/2024/content_123.htm",
            {"timeout": 30, "max_chars": 2_500_000},
        )]

    def test_scio_bundle_joins_only_same_article_pages(self, monkeypatch):
        import services.corpus.crawler as crawler

        base = "https://english.scio.gov.cn/whitepapers/2024/content_123.htm"
        pages = {
            base: """<html><head><title>Test White Paper</title></head><body>
                <a href='/whitepapers/2024/content_123_2.htm'>2</a>
                <a href='/whitepapers/2024/content_123_3.htm'>3</a>
                <a href='/whitepapers/2024/content_999.htm'>related</a>
                <!--enpcontent--><p>Page one official paragraph evidence repeated for validation.</p><!--/enpcontent-->
                </body></html>""",
            "https://english.scio.gov.cn/whitepapers/2024/content_123_2.htm":
                "<html><body><!--enpcontent--><p>Page two official paragraph evidence repeated for validation.</p><!--/enpcontent--></body></html>",
            "https://english.scio.gov.cn/whitepapers/2024/content_123_3.htm":
                "<html><body><!--enpcontent--><p>Page three official paragraph evidence repeated for validation.</p><!--/enpcontent--></body></html>",
        }
        monkeypatch.setattr(crawler, "fetch_document", lambda url, **kwargs: pages[url])
        result = crawler.fetch_scio_document(base)
        assert len(result.page_urls) == 3
        assert "Page one" in result.html and "Page three" in result.html
        assert "content_999" not in result.html

    def test_legacy_scio_hub_joins_declared_sections_not_attachment_shell(self):
        import services.corpus.crawler as crawler

        hub = "http://english.scio.gov.cn/node_8012622.html"
        shell = "http://english.scio.gov.cn/2019-06/03/content_74850375.htm"
        section_urls = [
            "http://english.scio.gov.cn/2019-06/03/content_74849920.htm",
            "http://english.scio.gov.cn/2019-06/03/content_74849926.htm",
        ]
        repeated_sections = "".join(
            f"<a href='{url}'>section</a>" for _ in range(3) for url in section_urls
        )
        hub_html = (
            "<html><head><title>Consultations White Paper</title></head><body>"
            "<a id='cn' href='http://english.scio.gov.cn/2019-06/03/content_74850125.htm'>中</a>"
            f"<a id='en' href='{shell}'>En</a>"
            f"{repeated_sections}</body></html>"
        )
        assert crawler._discover_scio_pages(hub, hub_html) == section_urls

    def test_scio_hub_includes_canonical_first_page_for_paginated_article(self):
        import services.corpus.crawler as crawler

        hub = "http://english.scio.gov.cn/node_9004328.html"
        first = "http://english.scio.gov.cn/whitepapers/2023/content_116710660.htm"
        second = "http://english.scio.gov.cn/whitepapers/2023/content_116710660_2.htm"
        hub_html = (
            "<html><body>"
            f"<a id='en' href='{first}'>En</a>"
            + "".join(f"<a href='{second}'>section</a>" for _ in range(3))
            + "</body></html>"
        )
        assert crawler._discover_scio_pages(hub, hub_html) == [first, second]

    def test_chinese_scio_challenge_uses_browser_automatically(self, monkeypatch):
        import services.corpus.crawler as crawler

        url = "http://www.scio.gov.cn/zfbps/zfbps_2279/202504/test.html"
        browser_html = (
            "<html><head><title>测试白皮书</title></head><body><p>"
            + "这是由官方站点返回并经过完整性校验的白皮书正文。" * 20
            + "</p></body></html>"
        )
        calls = []
        monkeypatch.setattr("tofu_search.fetch_url_bytes", lambda *args, **kwargs: None)

        def browser_fetch(candidate, **kwargs):
            calls.append((candidate, kwargs))
            return browser_html

        monkeypatch.setattr(crawler, "_fetch_scio_with_browser", browser_fetch)
        assert crawler.fetch_document(url) == browser_html
        assert calls == [(url, {"timeout": 30, "max_chars": 2_500_000})]

    def test_non_scio_failure_never_launches_browser(self, monkeypatch):
        import services.corpus.crawler as crawler

        monkeypatch.setattr("tofu_search.fetch_url_bytes", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            crawler,
            "_fetch_scio_with_browser",
            lambda *args, **kwargs: pytest.fail("browser must remain SCIO-only"),
        )
        with pytest.raises(crawler.CrawlError):
            crawler.fetch_document("https://example.invalid/document.html")

    def test_catalog_discovers_exact_pairs_from_official_hubs(self, monkeypatch):
        import services.corpus.crawler as crawler

        index_url = "http://english.scio.gov.cn/whitepapers/node_7247532.html"
        content_1 = "http://english.scio.gov.cn/whitepapers/2026/content_101.html"
        content_2 = "http://english.scio.gov.cn/whitepapers/2025/content_202.html"
        hub_1 = "http://english.scio.gov.cn/node_901.html"
        hub_2 = "http://english.scio.gov.cn/node_902.html"
        filler = "Official catalog and white-paper evidence. " * 10
        pages = {
            index_url: (
                f"<html><body><p>{filler}</p>"
                f"<a href='{content_1}'>Full text one</a>"
                f"<a href='{content_2}'>Full text two</a></body></html>"
            ),
            content_1: f"<script>var linkContent = '{hub_1}';</script>",
            content_2: f"<script>var linkContent = '{hub_2}';</script>",
            hub_1: (
                f"<html><head><title>White Paper One | english.scio.gov.cn</title></head>"
                f"<body><p>{filler}</p>"
                "<a id='cn' href='http://www.scio.gov.cn/zfbps/2026/one.html'>中</a>"
                f"<a id='en' href='{content_1}'>En</a></body></html>"
            ),
            hub_2: (
                f"<html><head><title>White Paper Two | english.scio.gov.cn</title></head>"
                f"<body><p>{filler}</p>"
                "<a id='cn' href='http://www.scio.gov.cn/zfbps/2025/two.html'>中</a>"
                f"<a id='en' href='{content_2}'>En</a></body></html>"
            ),
        }
        monkeypatch.setattr(
            crawler,
            "_fetch_official_english_raw",
            lambda url, **kwargs: pages[url],
        )
        monkeypatch.setattr(
            crawler,
            "fetch_document",
            lambda url, **kwargs: (
                f"<html><body><p>{filler}</p>"
                "<a href='/zfbps/2026/one.html'>one</a>"
                "<a href='/zfbps/2025/two.html'>two</a></body></html>"
            ),
        )
        pairs = crawler.discover_scio_pairs(limit=2)
        assert [pair.title for pair in pairs] == ["White Paper One", "White Paper Two"]
        assert pairs[0].zh_url.endswith("/zfbps/2026/one.html")
        assert pairs[0].en_url == content_1

    @pytest.mark.parametrize(("attachment_only", "expects_hub"), [(True, True), (False, False)])
    def test_pair_resolver_uses_section_hub_only_for_attachment_shell(
        self, monkeypatch, attachment_only, expects_hub
    ):
        import services.corpus.crawler as crawler

        hub = "http://english.scio.gov.cn/node_8012622.html"
        canonical = "http://english.scio.gov.cn/2019-06/03/content_74850375.htm"
        sections = [
            "http://english.scio.gov.cn/2019-06/03/content_74849920.htm",
            "http://english.scio.gov.cn/2019-06/03/content_74849926.htm",
        ]
        filler = "Official white-paper publication evidence. " * 12
        hub_html = (
            f"<html><head><title>Consultations White Paper</title></head><body><p>{filler}</p>"
            "<a id='cn' href='http://www.scio.gov.cn/zfbps/2019/paper.html'>中</a>"
            f"<a id='en' href='{canonical}'>En</a>"
            + "".join(f"<a href='{url}'>section</a>" for _ in range(3) for url in sections)
            + "</body></html>"
        )
        canonical_html = (
            "<html><body><!--enpcontent-->"
            + (
                "<p>Please see the attachment for the full text.</p>"
                "<a href='https://example.invalid/paper.doc'>Full text</a>"
                if attachment_only
                else f"<p>{filler}</p>"
            )
            + "<!--/enpcontent></body></html>"
        )
        pages = {hub: hub_html, canonical: canonical_html}
        monkeypatch.setattr(
            crawler,
            "_fetch_official_english_raw",
            lambda url, **kwargs: pages[url],
        )
        pair = crawler._resolve_scio_pair_from_english(hub)
        assert pair is not None
        assert pair.en_url == (hub if expects_hub else canonical)

    def test_access_challenge_is_never_stored(self):
        import services.corpus.crawler as crawler

        challenge = "<script>document.cookie='__jsl_clearance=x';location.href='/'</script>"
        with pytest.raises(crawler.CrawlError, match="access challenge"):
            crawler.validate_document_content(challenge, "http://www.scio.gov.cn/zfbps/x.htm")

    def test_attachment_only_announcement_is_never_stored(self):
        import services.corpus.crawler as crawler

        shell = (
            "<html><head><title>Full text</title></head><body>"
            "<p>Official navigation and publication information. " * 10
            + "</p><!--enpcontent--><p>Please see the attachment for the full text.</p>"
            "<a href='https://example.invalid/white-paper.doc'>Full text</a>"
            "<!--/enpcontent--></body></html>"
        )
        with pytest.raises(crawler.CrawlError, match="empty shell"):
            crawler.validate_document_content(
                shell,
                "http://english.scio.gov.cn/2019/content_74850375.htm",
            )


class TestScioUrlValidation:
    def test_accepts_official_document_pages(self):
        assert _validate_scio_url(
            "http://www.scio.gov.cn/zfbps/2024-01/01/content_123.htm", lang="zh"
        )
        assert _validate_scio_url(
            "https://english.scio.gov.cn/whitepapers/2024-01/01/content_123.htm",
            lang="en",
        )
        assert _validate_scio_url(
            "https://english.scio.gov.cn/node_8023479.html", lang="en"
        )

    @pytest.mark.parametrize(
        ("url", "lang"),
        [
            ("http://www.scio.gov.cn/zfbps/", "zh"),
            ("https://english.scio.gov.cn/whitepapers/node_7247532.html", "en"),
            ("https://english.scio.gov.cn.evil.example/content_123.htm", "en"),
        ],
    )
    def test_rejects_indexes_and_lookalike_hosts(self, url, lang):
        with pytest.raises(HTTPException, match="不能使用目录页"):
            _validate_scio_url(url, lang=lang)

    def test_saved_official_html_bypasses_network_not_validation(self):
        zh_html = (
            "<html><head><title>测试白皮书</title></head><body><p>"
            + "中国坚持以人民为中心的发展思想，持续推动高质量发展。" * 12
            + "</p></body></html>"
        )
        en_html = (
            "<html><head><title>Test White Paper</title></head><body><p>"
            + "China remains committed to a people-centered approach and continues "
              "to promote high-quality development. " * 12
            + "</p></body></html>"
        )
        result = asyncio.run(import_scio_pair(ScioImportRequest(
            zh_url="http://www.scio.gov.cn/zfbps/2024/test.htm",
            en_url="https://english.scio.gov.cn/whitepapers/2024/content_987.htm",
            zh_html=zh_html,
            en_html=en_html,
        )))
        assert result["source_pages"]["zh"] == [
            "http://www.scio.gov.cn/zfbps/2024/test.htm"
        ]
        assert result["source_pages"]["en"] == [
            "https://english.scio.gov.cn/whitepapers/2024/content_987.htm"
        ]
        assert result["ingest"]["sentence_pairs"] >= 1


class TestScioSyncPersistence:
    def test_same_range_refresh_is_seeded_from_completed_job(self):
        from services.corpus.sync import ScioSyncManager

        synced = [{
            "title": "Seed white paper",
            "zh_url": "http://www.scio.gov.cn/zfbps/1998/seed.html",
            "en_url": "http://english.scio.gov.cn/1998/seed.html",
            "pair_id": "seed-pair",
            "sentence_pairs": 17,
        }]
        with SessionLocal() as session:
            baseline = CorpusSyncJob(
                source="scio",
                status="completed",
                stage="complete",
                since_year=1998,
                through_year=1999,
                domain="government_white_paper",
                discovered=1,
                processed=1,
                succeeded=1,
                sentence_pairs=17,
                result={"synced": synced, "failed": [], "distillation": {}},
            )
            session.add(baseline)
            session.commit()
            baseline_id = baseline.id

        job_id = None
        try:
            job, created = ScioSyncManager().create_job(
                since_year=1998,
                through_year=1999,
                domain="government_white_paper",
            )
            job_id = job.id
            assert created is True
            assert job.processed == 1
            assert job.sentence_pairs == 17
            assert job.result["synced"] == synced
        finally:
            with SessionLocal() as session:
                if job_id:
                    created_job = session.get(CorpusSyncJob, job_id)
                    if created_job:
                        session.delete(created_job)
                original = session.get(CorpusSyncJob, baseline_id)
                if original:
                    session.delete(original)
                session.commit()
