import pytest

from services.retrieval.search import (
    MAX_QUERY_CHARS,
    LeakGuardError,
    QueryLeakGuard,
    _authority_for,
    official_search,
)

pytestmark = pytest.mark.unit


class TestQueryLeakGuard:
    def test_confidential_blocks_all_external(self):
        guard = QueryLeakGuard("CONFIDENTIAL")
        with pytest.raises(LeakGuardError):
            guard.check("高质量发展", official=True)
        with pytest.raises(LeakGuardError):
            guard.check("高质量发展", official=False)

    def test_internal_allows_official_only(self):
        guard = QueryLeakGuard("INTERNAL")
        assert guard.check("高质量发展", official=True) == "高质量发展"
        with pytest.raises(LeakGuardError):
            guard.check("高质量发展", official=False)

    def test_public_allows_both(self):
        guard = QueryLeakGuard("PUBLIC")
        assert guard.check("高质量发展", official=True)
        assert guard.check("高质量发展", official=False)

    def test_long_query_truncated(self):
        guard = QueryLeakGuard("PUBLIC")
        long_query = "推动高质量发展" * 40  # 280 chars
        result = guard.check(long_query, official=True)
        assert len(result) <= MAX_QUERY_CHARS


class TestOfficialTrustBoundary:
    def test_hostname_matching_rejects_lookalike_urls(self):
        assert _authority_for("https://english.scio.gov.cn/page") == "official_web"
        assert _authority_for("https://gov.cn.attacker.example/page") == "general_web"
        assert _authority_for("https://example.com/?next=gov.cn") == "general_web"

    def test_official_search_discards_backend_leakage(self, monkeypatch):
        async def fake_search(query, max_results):
            assert "site:gov.cn" in query
            return [
                {"title": "bad", "url": "https://shop.example/item", "snippet": "noise"},
                {"title": "good", "url": "https://english.www.gov.cn/policy", "snippet": "text"},
            ]

        monkeypatch.setattr("services.retrieval.search._search", fake_search)
        hits = __import__("asyncio").run(
            official_search("高质量发展", guard=QueryLeakGuard("PUBLIC"), max_results=3)
        )
        assert [hit["title"] for hit in hits] == ["good"]
