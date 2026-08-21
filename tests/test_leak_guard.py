import pytest

from services.retrieval.search import MAX_QUERY_CHARS, LeakGuardError, QueryLeakGuard

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
