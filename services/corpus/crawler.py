"""Corpus crawler (§11). Fetches official pages via tofu-search's fetch_url.
Failures raise CrawlError with the cause — observable, never silent (§51).
"""
from __future__ import annotations

import logging

logger = logging.getLogger("govtrans.corpus.crawler")

DEFAULT_SOURCES = {
    "scio_white_papers_zh": "http://www.scio.gov.cn/zfbps/zfbps_2279/",
    "scio_white_papers_en": "http://english.scio.gov.cn/whitepapers/",
}


class CrawlError(Exception):
    pass


def fetch_document(url: str, *, timeout: int = 30, max_chars: int = 400_000) -> str:
    """Fetch one page's text/HTML. Raises CrawlError on any failure."""
    from tofu_search import fetch_url

    try:
        content = fetch_url(url, max_chars=max_chars, timeout=timeout)
    except Exception as exc:
        raise CrawlError(f"fetch failed for {url}: {type(exc).__name__}: {exc}") from exc
    if not content:
        raise CrawlError(f"empty content fetched for {url}")
    return content
