"""Scripting tests — templated intro/outro, section glue, pronunciation, chapter structure."""

from __future__ import annotations

from pathlib import Path

from app.pipeline.parse import Story, parse_edition, real_stories
from app.pipeline.script import _spoken_date, build_intro, build_outro, build_scripts

FIXTURES = Path(__file__).parent / "fixtures"


def load(edition: str) -> str:
    return (FIXTURES / f"{edition}-2026-07-22.html").read_text(encoding="utf-8")


def test_ordinal_suffixes():
    assert "July 22nd" in _spoken_date("2026-07-22")
    assert "July 21st" in _spoken_date("2026-07-21")
    assert "July 23rd" in _spoken_date("2026-07-23")
    assert "July 11th" in _spoken_date("2026-07-11")
    assert "July 1st" in _spoken_date("2026-07-01")


def test_intro_format():
    assert (
        build_intro("ai", "2026-07-22", 17) == f"TLDR AI. {_spoken_date('2026-07-22')}. 17 stories."
    )
    assert build_intro("tech", "2026-07-22", 15).startswith("TLDR Tech. ")
    assert build_intro("infosec", "2026-07-22", 16).startswith("TLDR Infosec. ")


def test_chapter_structure_one_per_story():
    real = real_stories(parse_edition(load("ai")))
    chapters = build_scripts("ai", "2026-07-22", real)
    assert chapters[0].kind == "intro"
    assert chapters[-1].kind == "outro"
    stories = [c for c in chapters if c.kind == "story"]
    # Glue is NOT its own chapter — exactly one story chapter per parsed story.
    assert len(stories) == len(real) == 17
    assert [c.idx for c in chapters] == list(range(len(chapters)))


def test_section_glue_prepended():
    real = real_stories(parse_edition(load("ai")))
    stories = [c for c in build_scripts("ai", "2026-07-22", real) if c.kind == "story"]
    assert stories[0].script_text.startswith("First up — ")
    assert any(c.script_text.startswith("Next up — ") for c in stories)
    assert "&" not in stories[0].script_text.split(".")[0]  # "&" spoken as "and"


def test_pronunciation_applied_in_script():
    story = Story(
        section="X",
        headline="New API from a startup",
        summary="It uses GPT-4o on iOS.",
        url=None,
        read_time=None,
        is_sponsor=False,
    )
    text = [c for c in build_scripts("ai", "2026-07-22", [story]) if c.kind == "story"][
        0
    ].script_text
    assert "A P I" in text
    assert "G P T four oh" in text
    assert "i O S" in text


def test_outro_text():
    assert build_outro().startswith("That's the issue")
