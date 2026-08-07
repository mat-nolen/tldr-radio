"""Structure-driven story extraction (spec §4).

THE single point of failure — there is no LLM repair fallback. We key on **structure**,
never a hardcoded section list:

  - a story is an `<article class="mt-3">` → `<a href><h3>headline (N minute read)</h3></a>`
    followed by `<div class="newsletter-html">summary</div>`
  - a section divider is an `<h3 class="text-center font-bold">` between articles
  - sponsored items carry `(Sponsor)` in the headline → flagged, dropped before scripting

An unknown section name passes through untouched (degrades, doesn't crash). Zero non-sponsor
stories raises EmptyParseError so we never emit a short episode silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

READ_TIME_RE = re.compile(r"\((\d+)\s*minute read\)", re.IGNORECASE)
SPONSOR_RE = re.compile(r"\(\s*sponsor\s*\)", re.IGNORECASE)
_SUFFIX_RE = re.compile(r"\s*\((?:\d+\s*minute read|\s*sponsor\s*)\)\s*$", re.IGNORECASE)


@dataclass
class Story:
    section: str
    headline: str
    summary: str
    url: str | None
    read_time: str | None
    is_sponsor: bool


class EmptyParseError(Exception):
    """Raised when the parser finds zero non-sponsor stories — layout likely changed."""


def _is_section_header(el) -> bool:
    if el.name != "h3":
        return False
    classes = el.get("class") or []
    return "text-center" in classes and "font-bold" in classes


def _clean_headline(text: str) -> str:
    return _SUFFIX_RE.sub("", text).strip()


def parse_edition(html: str) -> list[Story]:
    """Extract stories (with is_sponsor flags) from archive HTML, in reading order.

    Raises EmptyParseError if no non-sponsor stories are found.
    """
    soup = BeautifulSoup(html, "lxml")
    current_section = ""
    stories: list[Story] = []

    # Document order: section-header <h3> and story <article> are siblings; an article's
    # own inner <h3> is not text-center/font-bold, so it's never mistaken for a divider.
    for el in soup.find_all(["h3", "article"]):
        if _is_section_header(el):
            text = el.get_text(" ", strip=True)
            if text:
                current_section = text
            continue
        if el.name == "article" and "mt-3" in (el.get("class") or []):
            h3 = el.find("h3")
            if h3 is None:
                continue
            raw = h3.get_text(" ", strip=True)
            match = READ_TIME_RE.search(raw)
            link = el.find("a", href=True)
            summary_el = el.find("div", class_="newsletter-html")
            stories.append(
                Story(
                    section=current_section,
                    headline=_clean_headline(raw),
                    summary=summary_el.get_text(" ", strip=True) if summary_el else "",
                    url=link["href"] if link else None,
                    read_time=f"{match.group(1)} minute read" if match else None,
                    is_sponsor=bool(SPONSOR_RE.search(raw)),
                )
            )

    if not any(not s.is_sponsor for s in stories):
        raise EmptyParseError("Parser found 0 non-sponsor stories — layout may have changed")
    return stories


def real_stories(stories: list[Story]) -> list[Story]:
    """Drop sponsored items (spec §4)."""
    return [s for s in stories if not s.is_sponsor]
