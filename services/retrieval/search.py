"""Official Search wrapper over tofu-search (§16) + QueryLeakGuard (§17).

- official_search(): restrict to the government allowlist via site: queries,
  tag every result with an authority level.
- web_search(): general search, authority "general_web".
- QueryLeakGuard: caps query length and blocks external search entirely for
  CONFIDENTIAL runs (official external search is off by default for
  CONFIDENTIAL; INTERNAL allows official search but not general web).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("govtrans.search")

OFFICIAL_ALLOWLIST = [
    "scio.gov.cn",
    "english.scio.gov.cn",
    "gov.cn",
    "xinhuanet.com",
]

MAX_QUERY_CHARS = 120


class LeakGuardError(Exception):
    pass


class QueryLeakGuard:
    """Prevents long source passages from leaking into external queries."""

    def __init__(self, confidentiality: str) -> None:
        self.confidentiality = confidentiality

    def check(self, query: str, *, official: bool) -> str:
        if self.confidentiality == "CONFIDENTIAL":
            raise LeakGuardError("CONFIDENTIAL 运行禁止一切外网检索")
        if self.confidentiality == "INTERNAL" and not official:
            raise LeakGuardError("INTERNAL 运行禁止通用网络检索，仅允许官方 allowlist")
        cleaned = " ".join(query.split())
        if len(cleaned) > MAX_QUERY_CHARS:
            cleaned = cleaned[:MAX_QUERY_CHARS]
            logger.info("QueryLeakGuard truncated query to %d chars", MAX_QUERY_CHARS)
        return cleaned


def _authority_for(url: str) -> str:
    for domain in OFFICIAL_ALLOWLIST:
        if domain in url:
            return "official_web"
    return "general_web"


async def _search(query: str, max_results: int) -> list[dict[str, Any]]:
    # tofu-search performs network IO; keep the event loop responsive.
    from tofu_search import perform_web_search

    return await asyncio.to_thread(perform_web_search, query, max_results=max_results)


async def official_search(
    query: str, *, guard: QueryLeakGuard, max_results: int = 6
) -> list[dict[str, Any]]:
    """Search only official government domains. Returns normalized evidence
    items with authority tags."""
    safe_query = guard.check(query, official=True)
    results: list[dict[str, Any]] = []
    # One site:-scoped query per allowlist domain keeps authority explicit.
    per_domain = max(1, max_results // len(OFFICIAL_ALLOWLIST)) + 1
    for domain in OFFICIAL_ALLOWLIST:
        try:
            hits = await _search(f"{safe_query} site:{domain}", per_domain)
        except Exception as exc:  # network failure of one domain must not kill the stage
            logger.warning("official_search failed for %s: %s", domain, exc)
            continue
        for hit in hits or []:
            url = hit.get("url", "")
            results.append({
                "title": hit.get("title", ""),
                "url": url,
                "snippet": (hit.get("full_content") or hit.get("snippet") or "")[:500],
                "authority": _authority_for(url),
            })
    # Authority first, then keep at most max_results.
    results.sort(key=lambda r: 0 if r["authority"] == "official_web" else 1)
    return results[:max_results]


async def web_search(
    query: str, *, guard: QueryLeakGuard, max_results: int = 6
) -> list[dict[str, Any]]:
    safe_query = guard.check(query, official=False)
    hits = await _search(safe_query, max_results)
    return [
        {
            "title": h.get("title", ""),
            "url": h.get("url", ""),
            "snippet": (h.get("full_content") or h.get("snippet") or "")[:500],
            "authority": _authority_for(h.get("url", "")),
        }
        for h in hits or []
    ]
