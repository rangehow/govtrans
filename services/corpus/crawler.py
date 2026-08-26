"""Safe corpus fetchers for official SCIO documents.

The corpus parser needs the original HTML, not a readability/Markdown
rendering. Raw HTML preserves headings, paragraphs, pagination links and the
exact fetched evidence for later audit. SCIO English white papers are often
split across ``content_<id>_N.htm`` pages, so ``fetch_scio_document`` discovers
and joins every page belonging to the same article before alignment.
"""
from __future__ import annotations

import html
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from urllib.parse import unquote, urljoin, urlparse, urlunparse

logger = logging.getLogger("govtrans.corpus.crawler")

DEFAULT_SOURCES = {
    "scio_white_papers_zh": "http://www.scio.gov.cn/zfbps/",
    "scio_white_papers_en": "https://english.scio.gov.cn/whitepapers/",
}

_HREF_RE = re.compile(r"\bhref\s*=\s*(['\"])(.*?)\1", re.I | re.S)
_ANCHOR_RE = re.compile(
    r"<a\b[^>]*\bhref\s*=\s*(['\"])(.*?)\1[^>]*>(.*?)</a>", re.I | re.S
)
_CONTENT_ID_RE = re.compile(r"content_(\d+)(?:_(\d+))?\.s?html?$", re.I)
_ENPCONTENT_RE = re.compile(
    r"<!--\s*enpcontent\s*-->(.*?)<!--\s*/enpcontent\s*-->", re.I | re.S
)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_SCRIPT_STYLE_RE = re.compile(r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_LINK_CONTENT_RE = re.compile(r"\blinkContent\s*=\s*(['\"])(.*?)\1", re.I | re.S)
_SCIO_ZH_HOSTS = {"scio.gov.cn", "www.scio.gov.cn"}
_SCIO_BROWSER_LOCK = Lock()
_SCIO_CLEARANCE_CACHE: dict[str, object] = {
    "cookies": {},
    "user_agent": "",
    "expires_at": 0.0,
}

# SCIO's Chinese site currently uses a two-stage JavaScript proof-of-work
# challenge. The second stage explicitly exits when browser automation is
# exposed through well-known navigator/window flags. We only normalize those
# flags for this public, allowlisted evidence source; the browser still runs
# the site's own challenge and accepts the server-issued clearance cookie.
_SCIO_BROWSER_INIT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {
  get: () => ['zh-CN', 'zh', 'en-US', 'en']
});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
if (!window.chrome) {
  Object.defineProperty(window, 'chrome', {value: {runtime: {}}});
}
try {
  delete window.webdriver;
  delete window.__nightmare;
  delete window.callPhantom;
  delete window._phantom;
} catch (_) {}
"""


class CrawlError(Exception):
    pass


@dataclass(frozen=True)
class FetchedDocument:
    html: str
    page_urls: tuple[str, ...]


@dataclass(frozen=True)
class ScioPairCandidate:
    """One exact Chinese/English pair declared by an official SCIO hub."""

    zh_url: str
    en_url: str
    title: str
    publish_year: int | None = None


def _decode_html(raw: bytes, content_type: str) -> str:
    charset_candidates: list[str] = []
    header_match = re.search(r"charset\s*=\s*([\w.-]+)", content_type, re.I)
    if header_match:
        charset_candidates.append(header_match.group(1))
    meta_match = re.search(
        br"charset\s*=\s*['\"]?\s*([A-Za-z0-9._-]+)", raw[:8_000], re.I
    )
    if meta_match:
        charset_candidates.append(meta_match.group(1).decode("ascii", errors="ignore"))
    charset_candidates.extend(["utf-8-sig", "gb18030"])

    seen: set[str] = set()
    for candidate in charset_candidates:
        encoding = candidate.casefold().replace("gb2312", "gb18030")
        if not encoding or encoding in seen:
            continue
        seen.add(encoding)
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def _visible_text(content: str) -> str:
    without_code = _SCRIPT_STYLE_RE.sub(" ", content)
    return " ".join(html.unescape(_TAG_RE.sub(" ", without_code)).split())


def _is_attachment_shell(content: str) -> bool:
    article_match = _ENPCONTENT_RE.search(content)
    article_html = article_match.group(1) if article_match else content
    article_visible = _visible_text(article_html).casefold()
    return (
        "please see the attachment" in article_visible
        and bool(re.search(r"href\s*=\s*(['\"])[^'\"]+\.docx?\b", article_html, re.I))
    )


def validate_document_content(content: str, url: str) -> None:
    """Reject access challenges, redirects and empty shells before storage."""
    lowered = content.casefold()
    challenge_markers = (
        "document.cookie",
        "challenge-platform",
        "enable javascript",
        "cache access denied",
        "__jsl_clearance",
    )
    visible = _visible_text(content)
    is_attachment_shell = _is_attachment_shell(content)
    is_redirect_shell = (
        "location.href" in lowered
        and len(content) < 8_000
        and len(visible) < 200
    )
    if (
        len(content.strip()) < 300
        or len(visible) < 100
        or is_attachment_shell
        or is_redirect_shell
        or sum(marker in lowered for marker in challenge_markers) >= 2
        or "cache access denied" in lowered
    ):
        raise CrawlError(
            f"official site returned an access challenge or empty shell instead of "
            f"the document for {url}; the automated browser fetcher could not "
            "complete this attempt"
        )


def _browser_launch_environment() -> dict[str, str]:
    """Build a child environment for Chromium without mutating the API process.

    Production images install Playwright's system libraries normally. The
    shared development workspace keeps those libraries beside ``chatui``;
    auto-detecting that prefix lets the local API use the same approved browser
    runtime without embedding an absolute machine-specific path in config.
    """
    child_env = dict(os.environ)
    explicit = os.environ.get("SCIO_BROWSER_LIBRARY_PATH", "").strip()
    library_dirs: list[Path] = []
    if explicit:
        library_dirs.extend(Path(item) for item in explicit.split(os.pathsep) if item)
    else:
        project_root = Path(__file__).resolve().parents[2]
        prefix = project_root.parent / "chatui" / ".tofu_chrome_deps" / "prefix"
        library_dirs.extend((prefix / "usr" / "lib64", prefix / "lib64"))
    existing_dirs = [str(path) for path in library_dirs if path.is_dir()]
    if existing_dirs:
        current = child_env.get("LD_LIBRARY_PATH", "")
        child_env["LD_LIBRARY_PATH"] = os.pathsep.join(
            existing_dirs + ([current] if current else [])
        )
    return child_env


def _playwright_proxy() -> dict[str, str] | None:
    """Translate standard proxy variables without ever logging credentials."""
    raw = (
        os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
    )
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    try:
        port = f":{parsed.port}" if parsed.port else ""
    except ValueError:
        return None
    proxy = {"server": f"{parsed.scheme}://{parsed.hostname}{port}"}
    if parsed.username:
        proxy["username"] = unquote(parsed.username)
        proxy["password"] = unquote(parsed.password or "")
    return proxy


def _clear_scio_clearance_cache() -> None:
    _SCIO_CLEARANCE_CACHE.update({"cookies": {}, "user_agent": "", "expires_at": 0.0})


def _fetch_scio_with_cached_clearance(
    url: str, *, timeout: int, max_chars: int
) -> str | None:
    """Reuse the short-lived server clearance inside this API process."""
    expires_at = float(_SCIO_CLEARANCE_CACHE.get("expires_at") or 0.0)
    cookies = _SCIO_CLEARANCE_CACHE.get("cookies")
    user_agent = str(_SCIO_CLEARANCE_CACHE.get("user_agent") or "")
    if not isinstance(cookies, dict) or not cookies or expires_at <= time.time():
        _clear_scio_clearance_cache()
        return None

    parsed = urlparse(url)
    target = urlunparse(parsed._replace(scheme="http", fragment=""))
    byte_limit = max(1_000_000, max_chars * 4)
    try:
        import httpx

        with httpx.stream(
            "GET",
            target,
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": user_agent},
            cookies=cookies,
        ) as response:
            response.raise_for_status()
            if (response.url.host or "").casefold() not in _SCIO_ZH_HOSTS:
                raise CrawlError("SCIO redirected outside the official host")
            chunks: list[bytes] = []
            byte_count = 0
            for chunk in response.iter_bytes():
                byte_count += len(chunk)
                if byte_count > byte_limit:
                    raise CrawlError("SCIO document exceeds the evidence limit")
                chunks.append(chunk)
            content = _decode_html(
                b"".join(chunks),
                response.headers.get("content-type", "text/html"),
            )
        validate_document_content(content, url)
        if len(content) > max_chars:
            raise CrawlError("SCIO document exceeds the evidence limit")
        logger.info("reused SCIO browser clearance for official evidence: %s", url)
        return content
    except Exception:
        _clear_scio_clearance_cache()
        return None


def _fetch_scio_with_browser(
    url: str, *, timeout: int, max_chars: int
) -> str:
    """Run SCIO's public JS challenge and capture the final document response.

    The HTTPS route exposed to this workspace currently passes the challenge
    but times out between the protection node and SCIO's origin. The legacy
    HTTP document route reaches the same official content and returns 200, so
    Chinese SCIO URLs are normalized to HTTP for the challenge transaction.
    """
    parsed = urlparse(url)
    if (parsed.hostname or "").casefold() not in _SCIO_ZH_HOSTS:
        raise CrawlError("browser challenge fetch is restricted to the official SCIO Chinese host")
    target = urlunparse(parsed._replace(scheme="http", fragment=""))
    timeout_ms = max(10_000, timeout * 1_000)
    max_bytes = max(1_000_000, max_chars * 4)

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - deployment packaging guard
        raise CrawlError(
            "SCIO browser fetcher is not installed; install the Playwright runtime"
        ) from exc

    observed_document_statuses: list[int] = []
    try:
        # Chromium is process-heavy and the official site should be treated
        # politely. Serialize challenge sessions in one API process.
        with _SCIO_BROWSER_LOCK:
            cached = _fetch_scio_with_cached_clearance(
                url,
                timeout=timeout,
                max_chars=max_chars,
            )
            if cached is not None:
                return cached
            with sync_playwright() as playwright:
                launch_options: dict = {
                    "headless": True,
                    "args": ["--disable-blink-features=AutomationControlled"],
                    "env": _browser_launch_environment(),
                }
                proxy = _playwright_proxy()
                if proxy:
                    launch_options["proxy"] = proxy
                browser = playwright.chromium.launch(**launch_options)
                try:
                    chrome_version = browser.version
                    user_agent = (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        f"Chrome/{chrome_version} Safari/537.36"
                    )
                    context = browser.new_context(
                        ignore_https_errors=True,
                        locale="zh-CN",
                        timezone_id="Asia/Shanghai",
                        viewport={"width": 1365, "height": 768},
                        user_agent=user_agent,
                    )
                    context.add_init_script(_SCIO_BROWSER_INIT)
                    page = context.new_page()

                    def remember(response) -> None:
                        if (
                            response.request.resource_type == "document"
                            and (urlparse(response.url).hostname or "").casefold()
                            in _SCIO_ZH_HOSTS
                        ):
                            observed_document_statuses.append(response.status)

                    page.on("response", remember)
                    first = page.goto(
                        target,
                        wait_until="domcontentloaded",
                        timeout=timeout_ms,
                    )
                    if first is not None and first.status == 200:
                        final = first
                    else:
                        final = page.wait_for_event(
                            "response",
                            predicate=lambda response: (
                                response.status == 200
                                and response.request.resource_type == "document"
                                and (urlparse(response.url).hostname or "").casefold()
                                in _SCIO_ZH_HOSTS
                            ),
                            timeout=timeout_ms,
                        )
                    raw = final.body()
                    content_type = final.headers.get("content-type", "text/html")
                    issued = [
                        cookie
                        for cookie in context.cookies()
                        if cookie.get("name")
                        and str(cookie.get("domain", "")).endswith("scio.gov.cn")
                        and str(cookie.get("name", "")).startswith("__jsl")
                    ]
                    cookie_values = {
                        str(cookie["name"]): str(cookie["value"])
                        for cookie in issued
                    }
                    clearance_expiries = [
                        float(cookie["expires"])
                        for cookie in issued
                        if str(cookie.get("name", "")).startswith("__jsl_clearance")
                        and float(cookie.get("expires") or 0) > time.time()
                    ]
                    if cookie_values:
                        _SCIO_CLEARANCE_CACHE.update({
                            "cookies": cookie_values,
                            "user_agent": user_agent,
                            "expires_at": (
                                min(clearance_expiries) - 30
                                if clearance_expiries
                                else time.time() + 1_800
                            ),
                        })
                finally:
                    browser.close()
    except PlaywrightTimeoutError as exc:
        if 502 in observed_document_statuses or 504 in observed_document_statuses:
            raise CrawlError(
                "SCIO browser challenge succeeded, but the official protection node "
                "timed out while contacting its origin; retry later"
            ) from exc
        raise CrawlError("SCIO browser challenge did not return a document before timeout") from exc
    except CrawlError:
        raise
    except Exception as exc:
        # Do not pass Playwright's launch diagnostics through the API: proxy
        # command lines can contain deployment details.
        raise CrawlError(
            f"SCIO browser fetcher failed to start or complete ({type(exc).__name__})"
        ) from exc

    if len(raw) > max_bytes:
        raise CrawlError(
            f"official document exceeds the configured {max_chars:,}-character "
            f"evidence limit for {url}; refusing to store a truncated copy"
        )
    content = _decode_html(raw, content_type)
    validate_document_content(content, url)
    if len(content) > max_chars:
        raise CrawlError(
            f"official document exceeds the configured {max_chars:,}-character "
            f"evidence limit for {url}; refusing to store a truncated copy"
        )
    logger.info("fetched SCIO Chinese evidence through browser challenge: %s", url)
    return content


def _fetch_candidates(url: str) -> list[str]:
    parsed = urlparse(url)
    candidates = [url]
    host = (parsed.hostname or "").casefold()
    if host == "english.scio.gov.cn" or host in _SCIO_ZH_HOSTS:
        alternate_scheme = "http" if parsed.scheme == "https" else "https"
        alternate = urlunparse(parsed._replace(scheme=alternate_scheme))
        if alternate not in candidates:
            candidates.append(alternate)
    return candidates


def fetch_document(url: str, *, timeout: int = 30, max_chars: int = 2_500_000) -> str:
    """Fetch and decode one page as raw HTML with the shared SSRF policy.

    Ordinary sources stay on the lightweight HTTP path. If and only if every
    attempt for the allowlisted Chinese SCIO host fails or returns its JS
    challenge, an isolated browser completes that challenge automatically.
    """
    errors: list[str] = []
    host = (urlparse(url).hostname or "").casefold()
    if host in _SCIO_ZH_HOSTS:
        # The challenge is consistently present and importing the general web
        # fetch stack is comparatively expensive. Go straight to the approved
        # browser path; retain lightweight HTTP below as a resilience fallback.
        try:
            return _fetch_scio_with_browser(
                url,
                timeout=timeout,
                max_chars=max_chars,
            )
        except CrawlError as exc:
            errors.append(str(exc))

    if host == "english.scio.gov.cn":
        try:
            content = _fetch_official_english_raw(
                url,
                timeout=timeout,
                max_chars=max_chars,
            )
            validate_document_content(content, url)
            return content
        except CrawlError as exc:
            errors.append(str(exc))

    from tofu_search import fetch_url_bytes

    for candidate in _fetch_candidates(url):
        try:
            fetched = fetch_url_bytes(
                candidate,
                max_bytes=max(1_000_000, max_chars * 4),
                timeout=timeout,
            )
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            continue
        if not fetched:
            errors.append(f"no body returned for {candidate}")
            continue
        raw, content_type = fetched
        content = _decode_html(raw, content_type)
        try:
            validate_document_content(content, url)
        except CrawlError as exc:
            errors.append(str(exc))
            continue
        if len(content) > max_chars:
            raise CrawlError(
                f"official document exceeds the configured {max_chars:,}-character "
                f"evidence limit for {url}; refusing to store a truncated copy"
            )
        return content
    detail = "; ".join(errors[-2:]) or "no response"
    raise CrawlError(f"fetch failed for {url}: {detail}")


def _fetch_official_english_raw(
    url: str, *, timeout: int = 30, max_chars: int = 2_500_000
) -> str:
    """Fetch an English SCIO page while allowing its small JS redirect shell."""
    parsed = urlparse(url)
    if (parsed.hostname or "").casefold() != "english.scio.gov.cn":
        raise CrawlError("SCIO catalog discovery is restricted to the official English host")
    # The site's HTTP route is its stable legacy publishing endpoint in this
    # environment; try it first and retain HTTPS as a fallback.
    http_url = urlunparse(parsed._replace(scheme="http", fragment=""))
    candidates = [http_url] + [item for item in _fetch_candidates(url) if item != http_url]
    errors: list[str] = []
    for candidate in candidates:
        try:
            import httpx

            byte_limit = max(1_000_000, max_chars * 4)
            with httpx.stream(
                "GET",
                candidate,
                follow_redirects=True,
                timeout=timeout,
                headers={"User-Agent": "GovTrans official-corpus fetcher/0.1"},
            ) as response:
                response.raise_for_status()
                if (response.url.host or "").casefold() != "english.scio.gov.cn":
                    raise CrawlError("English SCIO redirected outside the official host")
                chunks: list[bytes] = []
                byte_count = 0
                for chunk in response.iter_bytes():
                    byte_count += len(chunk)
                    if byte_count > byte_limit:
                        raise CrawlError("English SCIO catalog page exceeds the evidence limit")
                    chunks.append(chunk)
                raw = b"".join(chunks)
                content_type = response.headers.get("content-type", "text/html")
        except Exception as exc:
            errors.append(type(exc).__name__)
            continue
        if not raw:
            errors.append("empty response")
            continue
        content = _decode_html(raw, content_type)
        if len(content) > max_chars:
            raise CrawlError("English SCIO catalog page exceeds the evidence limit")
        return content
    raise CrawlError(
        "failed to read the official English SCIO catalog: "
        + (", ".join(errors[-2:]) or "no response")
    )


def _anchor_href_by_id(raw_html: str, anchor_id: str) -> str | None:
    match = re.search(
        rf"<a\b(?=[^>]*\bid\s*=\s*['\"]{re.escape(anchor_id)}['\"])(?=[^>]*"
        rf"\bhref\s*=\s*['\"]([^'\"]+)['\"])[^>]*>",
        raw_html,
        re.I | re.S,
    )
    return html.unescape(match.group(1).strip()) if match else None


def _clean_html_title(raw_html: str) -> str:
    match = _TITLE_RE.search(raw_html)
    if not match:
        return "SCIO White Paper"
    title = " ".join(html.unescape(_TAG_RE.sub(" ", match.group(1))).split())
    title = re.sub(r"\s*[|_-]\s*(?:中华人民共和国国务院新闻办公室|english\.scio\.gov\.cn)\s*$", "", title, flags=re.I)
    return title or "SCIO White Paper"


def _publication_year(*urls: str) -> int | None:
    """Read a four-digit year from SCIO's dated path segments.

    Chinese article paths use segments such as ``202606`` while the English
    site uses ``2026-06``. Restricting the match to a complete path segment
    avoids accidentally treating a CMS content id as a publication date.
    """
    for url in urls:
        for segment in urlparse(url).path.split("/"):
            match = re.fullmatch(r"((?:19|20)\d{2})(?:-?\d{2})?", segment)
            if match:
                return int(match.group(1))
    return None


def _publication_date_key(*urls: str) -> str | None:
    """Return YYYYMMDD when an official URL carries a complete publication date."""
    for url in urls:
        path = urlparse(url).path
        article_match = re.search(r"/t((?:19|20)\d{6})_\d+\.html?$", path, re.I)
        if article_match:
            return article_match.group(1)
        dated_path = re.search(
            r"/((?:19|20)\d{2})-(\d{2})/(\d{2})(?:/|$)", path
        )
        if dated_path:
            return "".join(dated_path.groups())
    return None


def _scio_year_archives(index_html: str, index_url: str) -> dict[int, str]:
    """Extract the year-labelled archive URLs declared by the Chinese index."""
    archives: dict[int, str] = {}
    for _quote, href, label_html in _ANCHOR_RE.findall(index_html):
        label = " ".join(html.unescape(_TAG_RE.sub(" ", label_html)).split())
        year_match = re.search(r"((?:19|20)\d{2})\s*年", label)
        if not year_match:
            continue
        absolute = html.unescape(urljoin(index_url, href.strip()))
        parsed = urlparse(absolute)
        if (
            (parsed.hostname or "").casefold() not in _SCIO_ZH_HOSTS
            or not parsed.path.casefold().startswith("/zfbps/ndhf/")
        ):
            continue
        archives[int(year_match.group(1))] = urlunparse(
            parsed._replace(scheme="http", fragment="")
        )
    return archives


def _scio_document_paths(raw_html: str, source_url: str) -> list[str]:
    """Return canonical-looking Chinese white-paper article paths in page order."""
    paths: list[str] = []
    seen: set[str] = set()
    for _quote, href in _HREF_RE.findall(raw_html):
        absolute = html.unescape(urljoin(source_url, href.strip()))
        parsed = urlparse(absolute)
        path = parsed.path.casefold()
        if (
            (parsed.hostname or "").casefold() in _SCIO_ZH_HOSTS
            and path.startswith("/zfbps/")
            and re.search(r"/t\d+_\d+\.html?$", path)
            and path not in seen
        ):
            seen.add(path)
            paths.append(path)
    return paths


def _archive_declared_pairs(
    raw_html: str, source_url: str, year: int
) -> tuple[list[ScioPairCandidate], int, list[tuple[str, str]]]:
    """Read adjacent Chinese/English full-text links from one annual page.

    From 2017 through 2024, the Chinese SCIO archive itself is an exact
    bilingual manifest: a Chinese full-text anchor is immediately followed by
    its English ``Full text`` anchor. Some labels omit that prefix, so a Latin
    label immediately following a Chinese label is accepted as the same
    first-party declaration. No titles are compared across independent pages.
    """
    pairs: list[ScioPairCandidate] = []
    chinese_entries: list[tuple[str, str]] = []
    document_links = 0
    pending_zh: tuple[str, str] | None = None
    for _quote, href, label_html in _ANCHOR_RE.findall(raw_html):
        absolute = html.unescape(urljoin(source_url, href.strip()))
        parsed = urlparse(absolute)
        path = parsed.path.casefold()
        if (
            (parsed.hostname or "").casefold() not in _SCIO_ZH_HOSTS
            or not path.startswith("/zfbps/")
            or not re.search(r"/t\d+_\d+\.html?$", path)
        ):
            continue
        label = " ".join(html.unescape(_TAG_RE.sub(" ", label_html)).split())
        if not label:
            continue
        document_links += 1
        normalized_url = urlunparse(parsed._replace(scheme="http", fragment=""))
        contains_chinese = bool(re.search(r"[\u3400-\u9fff]", label))
        looks_english = bool(re.search(r"[A-Za-z]{4}", label)) and not contains_chinese
        if pending_zh and looks_english:
            title = re.sub(
                r"^\s*full\s*text\s*[:：—-]?\s*", "", label, flags=re.I
            ).strip()
            pairs.append(
                ScioPairCandidate(
                    zh_url=pending_zh[0],
                    en_url=normalized_url,
                    title=title or pending_zh[1],
                    publish_year=year,
                )
            )
            pending_zh = None
        elif contains_chinese:
            pending_zh = (normalized_url, label)
            chinese_entries.append(pending_zh)
        else:
            pending_zh = None
    return pairs, document_links, chinese_entries


def _archive_title_matches_year(raw_html: str, year: int) -> bool:
    return f"{year}年" in _clean_html_title(raw_html)


def _fetch_archive_pages(
    archive_url: str,
    first_html: str,
    *,
    year: int,
    timeout: int,
) -> list[tuple[str, str]]:
    """Fetch real annual archive pages, stopping at SCIO's generic 404 shell."""
    pages = [(archive_url, first_html)]
    seen_bodies = {first_html}
    for page_number in range(1, 10):
        page_url = urljoin(archive_url.rstrip("/") + "/", f"index_{page_number}.html")
        try:
            raw_html = fetch_document(page_url, timeout=timeout)
        except CrawlError:
            break
        # Missing archive pages currently return the SCIO home page with 200.
        if not _archive_title_matches_year(raw_html, year) or raw_html in seen_bodies:
            break
        pages.append((page_url, raw_html))
        seen_bodies.add(raw_html)
    return pages


def _pair_title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", html.unescape(title).casefold())


def _resolve_scio_pair_from_english(
    candidate_url: str, *, timeout: int = 30
) -> ScioPairCandidate | None:
    """Resolve an English listing/full-text link to its exact bilingual hub.

    New hubs point ``cn`` directly at ``www.scio.gov.cn/zfbps/``. Older hubs
    point at a Chinese-language page on ``english.scio.gov.cn`` instead. Both
    are first-party SCIO evidence explicitly paired by the hub; accepting the
    legacy form is what makes a ten-year corpus possible without fuzzy titles.
    """
    raw = _fetch_official_english_raw(candidate_url, timeout=timeout)
    redirect_match = _LINK_CONTENT_RE.search(raw)
    hub_url = candidate_url
    if redirect_match:
        redirect_url = html.unescape(urljoin(candidate_url, redirect_match.group(2).strip()))
        if (urlparse(redirect_url).hostname or "").casefold() != "english.scio.gov.cn":
            return None
        hub_url = redirect_url
        raw = _fetch_official_english_raw(hub_url, timeout=timeout)
    try:
        validate_document_content(raw, hub_url)
    except CrawlError:
        return None

    zh_href = _anchor_href_by_id(raw, "cn")
    zh_declared_by_hub = bool(zh_href)
    en_href = _anchor_href_by_id(raw, "en")
    if not zh_href:
        # A few older templates omit the id but retain the exact official path.
        for _quote, href in _HREF_RE.findall(raw):
            absolute = html.unescape(urljoin(hub_url, href.strip()))
            parsed = urlparse(absolute)
            if (
                (parsed.hostname or "").casefold() in _SCIO_ZH_HOSTS
                and parsed.path.casefold().startswith("/zfbps/")
                and parsed.path.casefold().endswith((".htm", ".html"))
            ):
                zh_href = absolute
                break
    if not zh_href:
        return None
    zh_url = html.unescape(urljoin(hub_url, zh_href))
    zh_parsed = urlparse(zh_url)
    if (zh_parsed.hostname or "").casefold() == "english.scio.gov.cn":
        # Several recent ``cn`` anchors are tiny first-party redirect shells.
        # Prefer their declared SCIO Chinese destination when it stays inside
        # the official /zfbps/ allowlist.
        try:
            zh_shell = _fetch_official_english_raw(zh_url, timeout=timeout)
            zh_redirect = _LINK_CONTENT_RE.search(zh_shell)
            if zh_redirect:
                declared = html.unescape(
                    urljoin(zh_url, zh_redirect.group(2).strip())
                )
                declared_parsed = urlparse(declared)
                if (
                    (declared_parsed.hostname or "").casefold() in _SCIO_ZH_HOSTS
                    and declared_parsed.path.casefold().startswith("/zfbps/")
                ):
                    zh_url = declared
                    zh_parsed = declared_parsed
        except CrawlError:
            pass
    zh_host = (zh_parsed.hostname or "").casefold()
    is_chinese_catalog_document = (
        zh_host in _SCIO_ZH_HOSTS
        and zh_parsed.path.casefold().startswith("/zfbps/")
    )
    is_legacy_declared_chinese = (
        zh_declared_by_hub
        and zh_host == "english.scio.gov.cn"
        and zh_parsed.path.casefold().endswith((".htm", ".html"))
    )
    if not (is_chinese_catalog_document or is_legacy_declared_chinese):
        return None

    en_url = html.unescape(urljoin(hub_url, en_href or candidate_url))
    # Some 2018/2019 ``en`` anchors are attachment-only announcement pages.
    # Their bilingual hub is the authoritative table of contents and links to
    # every complete HTML section. Retain the hub URL so the document fetcher
    # can assemble those sections instead of silently ingesting three lines.
    hub_sections = _discover_scio_hub_sections(hub_url, raw)
    canonical_identity = _content_page_identity(en_url)
    section_article_ids = {
        identity[0]
        for page_url in hub_sections
        if (identity := _content_page_identity(page_url))
    }
    uses_separate_section_ids = (
        hub_sections
        and canonical_identity
        and canonical_identity[0] not in section_article_ids
    )
    if uses_separate_section_ids:
        try:
            canonical_raw = _fetch_official_english_raw(en_url, timeout=timeout)
        except CrawlError:
            canonical_raw = ""
        if canonical_raw and _is_attachment_shell(canonical_raw):
            en_url = hub_url
    en_parsed = urlparse(en_url)
    if (en_parsed.hostname or "").casefold() != "english.scio.gov.cn":
        return None
    return ScioPairCandidate(
        zh_url=urlunparse(zh_parsed._replace(scheme="http", fragment="")),
        en_url=urlunparse(en_parsed._replace(scheme="http", fragment="")),
        title=_clean_html_title(raw),
        publish_year=_publication_year(zh_url, en_url),
    )


def discover_scio_pairs(
    *,
    limit: int | None = None,
    since_year: int | None = None,
    through_year: int | None = None,
    timeout: int = 30,
) -> list[ScioPairCandidate]:
    """Discover exact bilingual pairs from SCIO's official catalogs.

    The English catalog is used as the pairing manifest because each full-text
    hub declares both its ``cn`` and ``en`` canonical URLs. The Chinese URL is
    then fetched from ``/zfbps/`` itself through the browser challenge path;
    no third-party mirror or fuzzy title matching is involved.

    When ``since_year`` is supplied, every Chinese year archive in the closed
    interval is read and every resolvable official bilingual pair is returned.
    ``limit`` remains available for small previews and backward-compatible API
    clients, but decade synchronization deliberately passes no fixed cap.
    """
    if limit is not None and (limit < 1 or limit > 200):
        raise ValueError("SCIO sync limit must be between 1 and 200")
    current_year = datetime.now(timezone.utc).year
    if limit is None and since_year is None:
        since_year = current_year - 9
        through_year = current_year
    if since_year is not None:
        through_year = through_year or current_year
        if since_year < 1991 or through_year > current_year + 1 or since_year > through_year:
            raise ValueError("SCIO year range is invalid")
    elif through_year is not None:
        raise ValueError("through_year requires since_year")

    chinese_catalog_order: dict[str, int] = {}
    chinese_catalog_year: dict[str, int] = {}
    archive_pairs: list[ScioPairCandidate] = []
    complete_archive_years: set[int] = set()
    archive_chinese_by_year: dict[int, list[tuple[str, str]]] = {}
    try:
        chinese_index_url = DEFAULT_SOURCES["scio_white_papers_zh"]
        chinese_index = fetch_document(chinese_index_url, timeout=timeout)
        catalog_pages: list[tuple[int | None, str, str]] = [
            (None, chinese_index_url, chinese_index)
        ]
        if since_year is not None and through_year is not None:
            archives = _scio_year_archives(chinese_index, chinese_index_url)
            missing_years: list[int] = []
            for year in range(through_year, since_year - 1, -1):
                archive_url = archives.get(year)
                if not archive_url:
                    missing_years.append(year)
                    continue
                first_archive_html = fetch_document(archive_url, timeout=timeout)
                year_pages = _fetch_archive_pages(
                    archive_url,
                    first_archive_html,
                    year=year,
                    timeout=timeout,
                )
                catalog_pages.extend(
                    (year, page_url, raw_html) for page_url, raw_html in year_pages
                )
                declared: list[ScioPairCandidate] = []
                chinese_entries: list[tuple[str, str]] = []
                document_link_count = 0
                for page_url, raw_html in year_pages:
                    page_pairs, page_document_links, page_chinese_entries = (
                        _archive_declared_pairs(
                        raw_html, page_url, year
                        )
                    )
                    declared.extend(page_pairs)
                    chinese_entries.extend(page_chinese_entries)
                    document_link_count += page_document_links
                archive_pairs.extend(declared)
                archive_chinese_by_year[year] = chinese_entries
                if declared and document_link_count == len(declared) * 2:
                    complete_archive_years.add(year)
            if missing_years:
                logger.warning(
                    "SCIO Chinese index omitted year archives: %s; exact English hubs remain authoritative",
                    ",".join(str(year) for year in missing_years),
                )

        for archive_year, source_url, raw_html in catalog_pages:
            for path in _scio_document_paths(raw_html, source_url):
                chinese_catalog_order.setdefault(path, len(chinese_catalog_order))
                inferred_year = archive_year or _publication_year(path)
                if inferred_year is not None:
                    chinese_catalog_year.setdefault(path, inferred_year)
    except CrawlError:
        # The exact bilingual mapping below remains authoritative even when a
        # catalog page is temporarily unavailable. Document fetches still go
        # to the Chinese /zfbps/ URLs themselves.
        logger.warning("SCIO Chinese catalog unavailable; using official bilingual hubs")

    candidates: list[str] = []
    index_url = "http://english.scio.gov.cn/whitepapers/node_7247532.html"
    try:
        index_html = _fetch_official_english_raw(index_url, timeout=timeout)
        validate_document_content(index_html, index_url)
        seen_candidates: set[str] = set()
        for _quote, href in _HREF_RE.findall(index_html):
            absolute = html.unescape(urljoin(index_url, href.strip()))
            parsed = urlparse(absolute)
            host = (parsed.hostname or "").casefold()
            path = parsed.path.casefold()
            if host != "english.scio.gov.cn" or "/m/" in path:
                continue
            is_full_text_candidate = "/whitepapers/" in path and "content_" in path
            is_document_hub = bool(re.fullmatch(r"/node_\d+\.html?", path))
            if not (is_full_text_candidate or is_document_hub):
                continue
            if path in seen_candidates:
                continue
            seen_candidates.add(path)
            candidates.append(absolute)
    except CrawlError:
        if not archive_pairs:
            raise
        logger.warning(
            "SCIO English hub index unavailable; retaining archive-declared bilingual pairs"
        )

    pairs: list[ScioPairCandidate] = list(archive_pairs)
    seen_zh: set[str] = {pair.zh_url for pair in pairs}
    seen_titles = {
        (pair.publish_year, _pair_title_key(pair.title))
        for pair in pairs
        if _pair_title_key(pair.title)
    }
    archive_pair_indexes = {
        (pair.publish_year, _pair_title_key(pair.title)): index
        for index, pair in enumerate(pairs)
        if _pair_title_key(pair.title)
    }
    # News/summary links can sit beside each full-text link, hence a generous
    # bounded scan. Every accepted result still comes from a bilingual hub.
    scan_cap = (
        len(candidates)
        if since_year is not None
        else min(len(candidates), max(24, (limit or 1) * 10))
    )
    resolved: list[tuple[int, ScioPairCandidate]] = []
    if since_year is None:
        for position, candidate in enumerate(candidates[:scan_cap]):
            try:
                pair = _resolve_scio_pair_from_english(candidate, timeout=timeout)
            except CrawlError:
                continue
            if pair:
                resolved.append((position, pair))
            if limit is not None and len(resolved) >= limit * 2:
                break
    else:
        # A decade currently spans dozens of hubs. Four bounded readers keep
        # discovery responsive without placing burst traffic on the official site.
        with ThreadPoolExecutor(max_workers=min(4, max(1, scan_cap))) as pool:
            futures = {
                pool.submit(_resolve_scio_pair_from_english, candidate, timeout=timeout): position
                for position, candidate in enumerate(candidates[:scan_cap])
            }
            for future in as_completed(futures):
                try:
                    pair = future.result()
                except CrawlError:
                    continue
                if pair:
                    resolved.append((futures[future], pair))
        resolved.sort(key=lambda item: item[0])

    for _position, pair in resolved:
        path = urlparse(pair.zh_url).path.casefold()
        # Archive membership is stronger evidence than a migrated CMS path:
        # some 2018 documents were republished under a 2022 URL segment.
        publish_year = chinese_catalog_year.get(path) or pair.publish_year
        if since_year is not None and through_year is not None:
            if publish_year is None or not since_year <= publish_year <= through_year:
                continue
        if publish_year != pair.publish_year:
            pair = ScioPairCandidate(
                zh_url=pair.zh_url,
                en_url=pair.en_url,
                title=pair.title,
                publish_year=publish_year,
            )
        # A rare current hub sends its Chinese link to a Xinhua redirect shell
        # instead of SCIO's own archive copy. Match it back to the annual
        # archive only when the English publication date selects exactly one
        # Chinese document; titles are never fuzzily compared.
        if (
            pair.publish_year is not None
            and (urlparse(pair.zh_url).hostname or "").casefold()
            == "english.scio.gov.cn"
        ):
            date_key = _publication_date_key(pair.en_url, pair.zh_url)
            dated_matches = [
                url
                for url, _label in archive_chinese_by_year.get(pair.publish_year, [])
                if _publication_date_key(url) == date_key
            ]
            if date_key and len(dated_matches) == 1:
                pair = ScioPairCandidate(
                    zh_url=dated_matches[0],
                    en_url=pair.en_url,
                    title=pair.title,
                    publish_year=pair.publish_year,
                )
        title_key = (pair.publish_year, _pair_title_key(pair.title))
        archive_index = archive_pair_indexes.get(title_key)
        if archive_index is not None:
            # Exact normalized titles from two independent first-party
            # manifests identify the same document. Keep the Chinese annual
            # archive URL, but prefer english.scio.gov.cn's complete paginated
            # HTML over old archive pages that occasionally contain only a
            # downloadable Word attachment.
            archived = pairs[archive_index]
            if (
                (urlparse(archived.en_url).hostname or "").casefold()
                in _SCIO_ZH_HOSTS
                and (urlparse(pair.en_url).hostname or "").casefold()
                == "english.scio.gov.cn"
            ):
                pairs[archive_index] = ScioPairCandidate(
                    zh_url=archived.zh_url,
                    en_url=pair.en_url,
                    title=archived.title,
                    publish_year=archived.publish_year,
                )
            continue
        if (
            pair.zh_url in seen_zh
            or pair.publish_year in complete_archive_years
            or (title_key[1] and title_key in seen_titles)
        ):
            continue
        seen_zh.add(pair.zh_url)
        if title_key[1]:
            seen_titles.add(title_key)
        pairs.append(pair)
        if limit is not None and len(pairs) >= limit and (
            not chinese_catalog_order
            or all(urlparse(item.zh_url).path.casefold() in chinese_catalog_order for item in pairs)
        ):
            break
    if not pairs:
        raise CrawlError("the official SCIO catalog returned no resolvable bilingual pairs")
    pairs.sort(
        key=lambda item: (
            -(item.publish_year or 0),
            chinese_catalog_order.get(
                urlparse(item.zh_url).path.casefold(), len(chinese_catalog_order) + 1
            ),
        )
    )
    return pairs if limit is None else pairs[:limit]


def _content_page_identity(url: str) -> tuple[str, int] | None:
    match = _CONTENT_ID_RE.search(urlparse(url).path)
    if not match:
        return None
    return match.group(1), int(match.group(2) or 1)


def _normalized_page_key(url: str) -> str:
    parsed = urlparse(url)
    return f"{(parsed.hostname or '').casefold()}{parsed.path.casefold()}"


def _discover_scio_hub_sections(source_url: str, raw_html: str) -> list[str]:
    """Extract the ordered full-text sections declared by a SCIO node hub.

    The hub template renders each real table-of-contents entry three times
    (contents, card title, and card introduction). The canonical ``en`` page
    is rendered twice and may be either page one of a paginated article or an
    attachment-only announcement. Repetition plus article identity lets us
    distinguish complete sections from surrounding related-news links without
    relying on translated title similarity.
    """
    source = urlparse(source_url)
    if (
        (source.hostname or "").casefold() != "english.scio.gov.cn"
        or not re.fullmatch(r"/node_\d+\.html?", source.path.casefold())
    ):
        return []

    ordered_urls: list[str] = []
    counts: dict[str, int] = {}
    canonical_by_key: dict[str, str] = {}
    for _quote, href in _HREF_RE.findall(raw_html):
        absolute = html.unescape(urljoin(source_url, href.strip()))
        parsed = urlparse(absolute)
        if (parsed.hostname or "").casefold() != "english.scio.gov.cn":
            continue
        if not _content_page_identity(absolute):
            continue
        normalized = urlunparse(parsed._replace(scheme="http", query="", fragment=""))
        key = _normalized_page_key(normalized)
        counts[key] = counts.get(key, 0) + 1
        if key not in canonical_by_key:
            canonical_by_key[key] = normalized
            ordered_urls.append(normalized)

    repeated = [url for url in ordered_urls if counts[_normalized_page_key(url)] >= 3]
    if not repeated:
        return []

    en_href = _anchor_href_by_id(raw_html, "en")
    if en_href:
        canonical_url = html.unescape(urljoin(source_url, en_href))
        canonical_identity = _content_page_identity(canonical_url)
        repeated_ids = {
            identity[0]
            for page_url in repeated
            if (identity := _content_page_identity(page_url))
        }
        # Recent papers use one content id with numbered pages. In that case
        # the hub's canonical ``en`` link is page one even when it appears only
        # twice. Older attachment shells have a different id and stay out.
        if canonical_identity and canonical_identity[0] in repeated_ids:
            canonical_parsed = urlparse(canonical_url)
            canonical_url = urlunparse(
                canonical_parsed._replace(scheme="http", query="", fragment="")
            )
            repeated.insert(0, canonical_url)

    deduplicated: list[str] = []
    seen: set[str] = set()
    for page_url in repeated:
        key = _normalized_page_key(page_url)
        if key not in seen:
            seen.add(key)
            deduplicated.append(page_url)
    return deduplicated[:50]


def _discover_scio_pages(source_url: str, raw_html: str) -> list[str]:
    """Find pages belonging to one English SCIO white-paper article."""
    source_host = (urlparse(source_url).hostname or "").casefold()
    hub_sections = _discover_scio_hub_sections(source_url, raw_html)
    if hub_sections:
        return hub_sections
    current_identity = _content_page_identity(source_url)
    grouped: dict[str, dict[str, int]] = {}

    for _quote, href in _HREF_RE.findall(raw_html):
        absolute = html.unescape(urljoin(source_url, href.strip()))
        parsed = urlparse(absolute)
        if (parsed.hostname or "").casefold() != "english.scio.gov.cn":
            continue
        identity = _content_page_identity(absolute)
        if not identity:
            continue
        article_id, page_number = identity
        if current_identity and article_id != current_identity[0]:
            continue
        if not current_identity and "/whitepapers/" not in parsed.path.casefold():
            continue
        grouped.setdefault(article_id, {})[absolute] = page_number

    if current_identity:
        article_id, page_number = current_identity
        grouped.setdefault(article_id, {})[source_url] = page_number
        selected_id = article_id
    elif source_host == "english.scio.gov.cn" and grouped:
        # A document hub can link to related articles. Its own white-paper ID
        # is the group with the most distinct pagination links.
        selected_id = max(grouped, key=lambda key: len(grouped[key]))
    else:
        return [source_url]

    pages = grouped.get(selected_id, {})
    if not pages:
        return [source_url]
    # Collapse http/https duplicates by path; fetch_document itself handles a
    # scheme fallback for the site's legacy certificate/proxy behavior.
    by_path: dict[str, tuple[str, int]] = {}
    for page_url, page_number in pages.items():
        path_key = urlparse(page_url).path.casefold()
        existing = by_path.get(path_key)
        if existing is None or page_url.startswith("https://"):
            by_path[path_key] = (page_url, page_number)
    return [item[0] for item in sorted(by_path.values(), key=lambda item: item[1])][:50]


def _article_fragment(raw_html: str) -> str:
    match = _ENPCONTENT_RE.search(raw_html)
    return match.group(1) if match else raw_html


def fetch_scio_document(
    url: str, *, timeout: int = 30, max_page_chars: int = 2_500_000
) -> FetchedDocument:
    """Fetch one complete SCIO document, joining all detected article pages."""
    first = fetch_document(url, timeout=timeout, max_chars=max_page_chars)
    page_urls = _discover_scio_pages(url, first)
    if page_urls == [url]:
        return FetchedDocument(first, (url,))

    pages: dict[str, str] = {}
    source_path = urlparse(url).path.casefold()
    for page_url in page_urls:
        if urlparse(page_url).path.casefold() == source_path:
            pages[page_url] = first
            break

    missing = [page_url for page_url in page_urls if page_url not in pages]
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(missing)))) as pool:
        futures = {
            pool.submit(
                fetch_document,
                page_url,
                timeout=timeout,
                max_chars=max_page_chars,
            ): page_url
            for page_url in missing
        }
        for future in as_completed(futures):
            page_url = futures[future]
            try:
                pages[page_url] = future.result()
            except CrawlError as exc:
                failures.append(f"{page_url}: {exc}")
    if failures:
        raise CrawlError(
            f"incomplete SCIO document: fetched {len(pages)}/{len(page_urls)} pages; "
            + "; ".join(failures[:2])
        )

    title_match = _TITLE_RE.search(first)
    title = html.unescape(_TAG_RE.sub("", title_match.group(1))).strip() if title_match else ""
    sections = [
        f'<section data-source-page="{html.escape(page_url, quote=True)}">'
        f"{_article_fragment(pages[page_url])}</section>"
        for page_url in page_urls
    ]
    combined = (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<title>{html.escape(title)}</title></head><body>"
        + "\n".join(sections)
        + "</body></html>"
    )
    validate_document_content(combined, url)
    return FetchedDocument(combined, tuple(page_urls))
