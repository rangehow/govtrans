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
from urllib.parse import urlsplit

logger = logging.getLogger("govtrans.search")

OFFICIAL_ALLOWLIST = [
    "scio.gov.cn",
    "english.scio.gov.cn",
    "gov.cn",
    "fmprc.gov.cn",
    "npc.gov.cn",
    "stats.gov.cn",
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
    try:
        hostname = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        hostname = ""
    for domain in OFFICIAL_ALLOWLIST:
        if hostname == domain or hostname.endswith(f".{domain}"):
            return "official_web"
    return "general_web"


async def _search(query: str, max_results: int) -> list[dict[str, Any]]:
    # Official terminology lookup only needs result titles/snippets and URLs.
    # Full-page fetch + neural reranking made each small term query take up to
    # 30 seconds while adding no trust: the hostname filter below remains the
    # authority boundary. Restricting this path to the fast Bing adapter keeps
    # research optional and bounded instead of dominating translation latency.
    from tofu_search import perform_web_search

    def search_snippets():
        try:
            return perform_web_search(
                query,
                max_results=max_results,
                fetch_pages=False,
                filter_pages=False,
                rerank=False,
                engines=["Bing"],
            )
        except TypeError:  # compatibility with older tofu-search releases
            return perform_web_search(query, max_results=max_results)

    # tofu-search performs network IO; keep the event loop responsive.
    return await asyncio.to_thread(search_snippets)


async def official_search(
    query: str, *, guard: QueryLeakGuard, max_results: int = 6
) -> list[dict[str, Any]]:
    """Search only official government domains. Returns normalized evidence
    items with authority tags."""
    safe_query = guard.check(query, official=True)
    site_clause = " OR ".join(f"site:{domain}" for domain in OFFICIAL_ALLOWLIST)
    try:
        hits = await _search(f"{safe_query} ({site_clause})", max(max_results * 3, 12))
    except Exception as exc:  # research outage degrades to unverified terminology
        logger.warning("official_search failed: %s", exc)
        return []

    # Search backends can ignore site: operators. The hostname check is the
    # actual trust boundary; non-official results must never become evidence.
    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for hit in hits or []:
        url = str(hit.get("url") or "")
        if _authority_for(url) != "official_web" or url in seen_urls:
            continue
        seen_urls.add(url)
        results.append({
            "title": hit.get("title", ""),
            "url": url,
            "snippet": (hit.get("full_content") or hit.get("snippet") or "")[:500],
            "authority": "official_web",
        })
        if len(results) >= max_results:
            break
    return results


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
