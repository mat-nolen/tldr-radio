"""Fetch-layer tests — telling "no issue today" apart from "the layout broke".

On a day with no issue tldr.tech does NOT 404: it answers 200 and redirects
/<edition>/<date> to the edition landing page. Verified live for all three editions on
Sat 2026-07-25. Confusing that with a layout regression is what produced 3-6 loud failures
every weekend, so both directions are pinned here.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from app.pipeline.fetch import (
    NotPublishedError,
    cache_path,
    fetch_archive,
    is_issue_page,
    not_published_reason,
)

DATE = "2026-07-25"
ISSUE_HTML = (
    '<html><head><title>Stripe talks 💰, ChatGPT Health ⚕️</title></head><body>'
    f"<h1>TLDR AI {DATE}</h1>"
    '<article class="mt-3"><a href="https://ex.com/x"><h3>A thing (3 minute read)</h3></a>'
    '<div class="newsletter-html">Summary.</div></article></body></html>'
)
HOMEPAGE_HTML = (
    "<html><head><title>TLDR - A Byte Sized Daily Tech Newsletter</title></head>"
    "<body><h1>Keep up with tech in 5 minutes</h1><p>Weekend</p></body></html>"
)
# The ai/infosec weekend landing page: no <title> at all, no stories, no dated H1.
LANDING_HTML = "<html><body><h1>TLDR AI</h1><p>Join 500,000 readers</p></body></html>"


def fake_response(monkeypatch, html: str, final_url: str, status: int = 200) -> None:
    """Answer every AsyncClient.get with `html`, as if it landed on `final_url`."""

    async def get(self, url, **kwargs):  # noqa: ANN001, ANN202 — test double
        return httpx.Response(status, text=html, request=httpx.Request("GET", final_url))

    monkeypatch.setattr(httpx.AsyncClient, "get", get)


def fetch(edition: str, date: str, cache_dir: Path) -> str:
    return asyncio.run(fetch_archive(edition, date, cache_dir))


# ---------------------------------------------------------------- signal-level checks
def test_redirect_away_means_not_published():
    reason = not_published_reason(LANDING_HTML, "ai", DATE, "https://tldr.tech/ai")
    assert reason and "redirected" in reason


def test_homepage_served_at_issue_url_means_not_published():
    reason = not_published_reason(HOMEPAGE_HTML, "tech", DATE, f"https://tldr.tech/tech/{DATE}")
    assert reason and "homepage" in reason


def test_real_issue_is_published():
    assert not_published_reason(ISSUE_HTML, "ai", DATE, f"https://tldr.tech/ai/{DATE}") is None


def test_trailing_slash_is_still_the_issue_url():
    assert not_published_reason(ISSUE_HTML, "ai", DATE, f"https://tldr.tech/ai/{DATE}/") is None


def test_zero_stories_is_not_a_not_published_signal():
    """A dated issue that parses to nothing must stay LOUD — that's the layout alarm."""
    broken = f"<html><body><h1>TLDR AI {DATE}</h1><p>totally new layout</p></body></html>"
    assert not_published_reason(broken, "ai", DATE, f"https://tldr.tech/ai/{DATE}") is None


# ---------------------------------------------------------------- is_issue_page
@pytest.mark.parametrize("html", [HOMEPAGE_HTML, LANDING_HTML])
def test_landing_pages_are_not_issue_pages(html: str):
    assert not is_issue_page(html, DATE)


def test_issue_page_recognized_by_either_marker():
    assert is_issue_page(ISSUE_HTML, DATE)                                     # both markers
    assert is_issue_page(f"<h1>TLDR {DATE}</h1><p>no articles yet</p>", DATE)  # dated H1 only
    assert is_issue_page('<article class="mt-3"><h3>x</h3></article>', DATE)    # article only


# ---------------------------------------------------------------- fetch_archive
def test_weekend_redirect_raises_and_is_never_cached(tmp_path: Path, monkeypatch):
    fake_response(monkeypatch, LANDING_HTML, "https://tldr.tech/ai")
    with pytest.raises(NotPublishedError):
        fetch("ai", DATE, tmp_path)
    # Caching it would mask a real fetch for this date forever.
    assert not cache_path(tmp_path, "ai", DATE).exists()


def test_published_issue_is_returned_and_cached(tmp_path: Path, monkeypatch):
    fake_response(monkeypatch, ISSUE_HTML, f"https://tldr.tech/ai/{DATE}")
    assert fetch("ai", DATE, tmp_path) == ISSUE_HTML
    assert cache_path(tmp_path, "ai", DATE).read_text(encoding="utf-8") == ISSUE_HTML


def test_404_still_raises_not_published(tmp_path: Path, monkeypatch):
    fake_response(monkeypatch, "not found", "https://tldr.tech/ai/nope", status=404)
    with pytest.raises(NotPublishedError):
        fetch("ai", DATE, tmp_path)


def test_cached_issue_is_served_without_network(tmp_path: Path, monkeypatch):
    cache_path(tmp_path, "ai", DATE).write_text(ISSUE_HTML, encoding="utf-8")
    fake_response(monkeypatch, "SHOULD NOT BE FETCHED", f"https://tldr.tech/ai/{DATE}")
    assert fetch("ai", DATE, tmp_path) == ISSUE_HTML


def test_poisoned_cache_is_discarded_and_refetched(tmp_path: Path, monkeypatch):
    """A landing page cached under the issue key (pre-fix runs did this) must not stick."""
    cached = cache_path(tmp_path, "ai", DATE)
    cached.write_text(LANDING_HTML, encoding="utf-8")
    fake_response(monkeypatch, ISSUE_HTML, f"https://tldr.tech/ai/{DATE}")
    assert fetch("ai", DATE, tmp_path) == ISSUE_HTML  # re-fetched, not served from cache
    assert cached.read_text(encoding="utf-8") == ISSUE_HTML


def test_layout_regression_is_cached_for_offline_debugging(tmp_path: Path, monkeypatch):
    """A dated issue that parses to nothing is kept on disk — the parser fails loudly later."""
    broken = f"<html><body><h1>TLDR AI {DATE}</h1><p>new layout</p></body></html>"
    fake_response(monkeypatch, broken, f"https://tldr.tech/ai/{DATE}")
    assert fetch("ai", DATE, tmp_path) == broken
    assert cache_path(tmp_path, "ai", DATE).exists()
