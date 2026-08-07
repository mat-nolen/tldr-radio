"""Parser tests — the parser is the single point of failure, so pin it hard.

Fixtures are real tldr.tech archive pages saved 2026-07-22. Counts are hand-verified.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.pipeline.parse import EmptyParseError, parse_edition, real_stories

FIXTURES = Path(__file__).parent / "fixtures"

# Hand-counted real (non-sponsor) story totals for the 2026-07-22 fixtures.
EXPECTED_COUNTS = {"tech": 15, "ai": 17, "infosec": 16}
EXPECTED_SPONSORS = {"tech": 3, "ai": 2, "infosec": 2}


def load(edition: str) -> str:
    return (FIXTURES / f"{edition}-2026-07-22.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("edition,count", EXPECTED_COUNTS.items())
def test_real_story_count(edition: str, count: int):
    assert len(real_stories(parse_edition(load(edition)))) == count


@pytest.mark.parametrize("edition,n_sponsors", EXPECTED_SPONSORS.items())
def test_sponsors_flagged_and_dropped(edition: str, n_sponsors: int):
    parsed = parse_edition(load(edition))
    assert sum(s.is_sponsor for s in parsed) == n_sponsors
    assert all(not s.is_sponsor for s in real_stories(parsed))


# The other 11 newsletters use the same archive layout, so the parser should need no changes
# for them. Real pages, saved 2026-08-05/06, counts hand-verified — this is what proves that
# enabling an edition is only ever a catalog entry.
NEW_EDITIONS = {
    "dev-2026-08-06": (13, 1, "Articles & Tutorials"),
    "marketing-2026-08-05": (12, 1, "Strategies & Tactics"),
    "hardware-2026-08-05": (14, 0, "Engineering and Applications"),
}


@pytest.mark.parametrize("fixture,expected", NEW_EDITIONS.items())
def test_newer_editions_parse_unchanged(fixture: str, expected: tuple[int, int, str]):
    count, n_sponsors, section = expected
    parsed = parse_edition((FIXTURES / f"{fixture}.html").read_text(encoding="utf-8"))
    real = real_stories(parsed)
    assert len(real) == count
    assert sum(s.is_sponsor for s in parsed) == n_sponsors
    assert section in {s.section for s in real}
    assert all(s.headline and s.summary for s in real)


def test_sections_correct_per_edition():
    # Editions disagree on section names — the parser must pass them through, not hardcode.
    ai_sections = {s.section for s in real_stories(parse_edition(load("ai")))}
    assert {"Headlines & Launches", "Engineering & Research", "Quick Links"} <= ai_sections
    infosec_sections = {s.section for s in real_stories(parse_edition(load("infosec")))}
    assert {"Attacks & Vulnerabilities", "Launches & Tools"} <= infosec_sections


def test_story_fields_extracted():
    first = real_stories(parse_edition(load("ai")))[0]
    assert first.headline == "OpenAI Models Escaped a Cybersecurity Test"
    assert first.read_time == "5 minute read"
    assert first.url and first.url.startswith("http")
    assert first.summary  # non-empty
    assert "(Sponsor)" not in first.headline
    assert "minute read" not in first.headline


def test_no_web_chrome_leaks():
    real = real_stories(parse_edition(load("tech")))
    blob = " ".join(s.headline + " " + s.summary for s in real).lower()
    chrome_phrases = (
        "view in browser",
        "manage your subscription",
        "unsubscribe",
        "advertise with us",
    )
    for chrome in chrome_phrases:
        assert chrome not in blob


def test_unknown_section_degrades_not_crashes():
    html = (
        '<h3 class="text-center font-bold">Wormholes &amp; Portals</h3>'
        '<article class="mt-3"><a class="font-bold" href="https://ex.com/x">'
        "<h3>A brand new thing (3 minute read)</h3></a>"
        '<div class="newsletter-html">Some summary text.</div></article>'
    )
    stories = parse_edition(html)
    assert len(stories) == 1
    assert stories[0].section == "Wormholes & Portals"
    assert stories[0].headline == "A brand new thing"
    assert stories[0].read_time == "3 minute read"


def test_loud_failure_on_broken_layout():
    with pytest.raises(EmptyParseError):
        parse_edition("<html><body><p>completely different layout</p></body></html>")


def test_loud_failure_when_only_sponsor():
    html = (
        '<article class="mt-3"><a href="https://s.com"><h3>Buy now (Sponsor)</h3></a>'
        '<div class="newsletter-html">ad copy</div></article>'
    )
    with pytest.raises(EmptyParseError):
        parse_edition(html)
