"""tldr.tech archive client + on-disk page cache (spec §4).

Every *issue* fetch is cached to disk so a bad parse can be re-run offline after a fix, and
so re-runs never re-fetch. Responses that aren't an issue at all — a 404, or the landing-page
redirect served on weekends/holidays — raise NotPublishedError and are never cached. Network
errors are retried with backoff.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

ARCHIVE_URL = "https://tldr.tech/{edition}/{date}"
USER_AGENT = "TLDR-Radio/0.1 (+local, single-user)"

# Every real issue renders its own date in the page H1 — "TLDR 2026-07-24",
# "TLDR AI 2026-07-24", "TLDR Information Security 2026-07-24" (verified across 15 pages).
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
# A story-shaped article — the parser's unit of work (see parse.py).
_STORY_ARTICLE_RE = re.compile(r'<article[^>]*\bclass="[^"]*\bmt-3\b', re.IGNORECASE)
# The marketing homepage tldr.tech serves at "/" — a positive fingerprint, never an issue.
_HOMEPAGE_TITLE_RE = re.compile(r"<title[^>]*>\s*TLDR\s*[-–—]\s*A Byte Sized", re.IGNORECASE)


class NotPublishedError(Exception):
    """Raised when tldr.tech has no issue for this edition+date.

    Covers a 404 *and* the more common case: on a day with no issue the archive answers 200
    and redirects to the edition landing page (see `not_published_reason`).
    """


def cache_path(cache_dir: Path, edition: str, date: str) -> Path:
    return cache_dir / f"{edition}-{date}.html"


def is_issue_page(html: str, date: str) -> bool:
    """True if this HTML looks like the dated issue page for `date`.

    Two independent positive markers — the dated H1, or at least one story-shaped article.
    Either alone suffices, so a change to one doesn't make us discard a good page.
    """
    if any(date in h1 for h1 in _H1_RE.findall(html)):
        return True
    return bool(_STORY_ARTICLE_RE.search(html))


def not_published_reason(html: str, edition: str, date: str, final_url: str) -> str | None:
    """Why this 200 response isn't the issue for edition+date — or None if it is.

    On a day with no issue (weekend, holiday, or simply not posted yet) tldr.tech does **not**
    404. It answers 200 and redirects `/{edition}/{date}` away — to `/{edition}` or `/` — and
    serves the landing page. Confirmed live for all three editions on Sat 2026-07-25 and for
    pre-archive dates; a published issue never redirects.

    Deliberately *not* a signal here: "zero stories". A page that really is the dated issue but
    parses to nothing is the genuine layout-regression alarm and must stay loud (EmptyParseError).
    """
    want = f"/{edition}/{date}"
    if (urlsplit(final_url).path.rstrip("/") or "/") != want:
        return f"redirected to {final_url} — no issue at {want}"
    if _HOMEPAGE_TITLE_RE.search(html):
        return "served the tldr.tech homepage, not a dated issue"
    return None


async def fetch_archive(
    edition: str,
    date: str,
    cache_dir: Path,
    *,
    use_cache: bool = True,
    max_retries: int = 3,
) -> str:
    """GET the archive page for `edition`+`date`, caching issue HTML to `cache_dir`.

    Returns the page HTML (from cache if present). Raises NotPublishedError when there is no
    issue for that date, or the last httpx error after `max_retries` attempts.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_path(cache_dir, edition, date)
    if use_cache and cached.exists():
        html = cached.read_text(encoding="utf-8")
        if is_issue_page(html, date):
            log.info("cache hit: %s", cached)
            return html
        # Before the landing-page check below existed, a weekend fetch cached the landing page
        # under the issue's key, where it masqueraded as a broken issue on every later run.
        # Drop it and re-fetch so the live response decides.
        log.info("discarding non-issue cache %s", cached)
        cached.unlink(missing_ok=True)

    url = ARCHIVE_URL.format(edition=edition, date=date)
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(
                timeout=20.0,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            ) as client:
                resp = await client.get(url)
            if resp.status_code == 404:
                raise NotPublishedError(f"{edition} not published for {date}")
            resp.raise_for_status()
            html = resp.text
            reason = not_published_reason(html, edition, date, str(resp.url))
            if reason is not None:
                # Never cache a non-issue page under the issue's key.
                raise NotPublishedError(f"{edition} has no issue for {date} — {reason}")
            cached.write_text(html, encoding="utf-8")
            log.info("fetched %s (%d bytes) → %s", url, len(html), cached)
            return html
        except NotPublishedError:
            raise
        except httpx.HTTPError as exc:
            last_exc = exc
            log.warning("fetch %d/%d failed for %s: %s", attempt, max_retries, url, exc)
            if attempt < max_retries:
                await asyncio.sleep(0.5 * 2**attempt)

    assert last_exc is not None
    raise last_exc
