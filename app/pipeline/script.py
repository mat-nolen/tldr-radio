"""Deterministic TTS scripting — cleanup, section glue, templated intro/outro (spec §6, §16).

No LLM. Section announcements are PREPENDED to the first story of each section (glue is not
its own chapter → one chapter == one story). Numbers/dates lean on Kokoro's normalizer
(verified in Spike #0); the pronunciation dictionary covers jargon.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_cls

from ..editions import name_for
from .parse import Story
from .pronounce import apply_pronunciations


@dataclass
class ScriptedChapter:
    idx: int
    kind: str  # 'intro' | 'story' | 'outro'
    section: str | None
    headline: str | None
    summary_source: str | None
    script_text: str
    url: str | None
    read_time: str | None


def _ordinal(n: int) -> str:
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _spoken_date(iso_date: str) -> str:
    """'2026-07-22' → 'Wednesday, July 22nd' (Kokoro reads the ordinal naturally)."""
    d = date_cls.fromisoformat(iso_date)
    return d.strftime(f"%A, %B {_ordinal(d.day)}")


def _spoken_section(section: str) -> str:
    return section.replace("&", "and").strip()


def build_intro(edition: str, issue_date: str, n_stories: int) -> str:
    return f"{name_for(edition)}. {_spoken_date(issue_date)}. {n_stories} stories."


def build_outro() -> str:
    return "That's the issue. Thanks for listening, and we'll see you next time."


def build_story_script(story: Story, glue: str | None = None) -> str:
    parts: list[str] = []
    if glue:
        parts.append(glue)
    parts.append(apply_pronunciations(story.headline).rstrip(".") + ".")
    if story.summary:
        parts.append(apply_pronunciations(story.summary))
    return " ".join(parts)


def build_scripts(edition: str, issue_date: str, stories: list[Story]) -> list[ScriptedChapter]:
    """Turn parsed stories into ordered chapters: intro, one per story (with glue), outro."""
    real = [s for s in stories if not s.is_sponsor]

    chapters: list[ScriptedChapter] = [
        ScriptedChapter(
            idx=0,
            kind="intro",
            section=None,
            headline=None,
            summary_source=None,
            script_text=build_intro(edition, issue_date, len(real)),
            url=None,
            read_time=None,
        )
    ]

    prev_section: str | None = None
    for i, story in enumerate(real, start=1):
        glue: str | None = None
        if story.section != prev_section:
            lead = "First up" if prev_section is None else "Next up"
            glue = f"{lead} — {_spoken_section(story.section)}." if story.section else None
            prev_section = story.section
        chapters.append(
            ScriptedChapter(
                idx=i,
                kind="story",
                section=story.section,
                headline=story.headline,
                summary_source=story.summary,
                script_text=build_story_script(story, glue),
                url=story.url,
                read_time=story.read_time,
            )
        )

    chapters.append(
        ScriptedChapter(
            idx=len(real) + 1,
            kind="outro",
            section=None,
            headline=None,
            summary_source=None,
            script_text=build_outro(),
            url=None,
            read_time=None,
        )
    )
    return chapters
