"""Deterministic TTS scripting — cleanup, section glue, templated intro/outro (spec §6, §16).

No LLM. Section announcements are PREPENDED to the first story of each section (glue is not
its own chapter → one chapter == one story). Numbers/dates lean on Kokoro's normalizer
(verified in Spike #0); the pronunciation dictionary covers jargon.

Sponsored items, when they reach here, become their own `sponsor` chapters in the position
they hold in the newsletter — so a listener can see one coming and skip it the way a reader's
eye slides past it. Whether they reach here at all is the caller's decision: the worker drops
them before scripting when the setting is off, which is why there is no flag on this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_cls

from ..editions import name_for
from .parse import Story
from .pronounce import apply_pronunciations

#: Spoken ad disclosure, prepended to every sponsor read.
SPONSOR_LEAD = "A word from our sponsor."


@dataclass
class ScriptedChapter:
    idx: int
    kind: str  # 'intro' | 'story' | 'sponsor' | 'outro'
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


def build_sponsor_script(story: Story) -> str:
    """A sponsor read, opened with a spoken disclosure.

    The lead-in is deliberate: the different voice already signals "this is paid", but the
    voice is a setting a listener can change, so the disclosure is also carried in the words.
    """
    parts: list[str] = [SPONSOR_LEAD]
    parts.append(apply_pronunciations(story.headline).rstrip(".") + ".")
    if story.summary:
        parts.append(apply_pronunciations(story.summary))
    return " ".join(parts)


def build_scripts(edition: str, issue_date: str, stories: list[Story]) -> list[ScriptedChapter]:
    """Turn parsed items into ordered chapters: intro, one per item (with glue), outro.

    Sponsored items become `sponsor` chapters where they sit in the newsletter. They take no
    section glue and no story number — they are interstitial, so the section run and the
    "story n of m" count both step over them and stay true to the printed issue.
    """
    n_stories = sum(1 for s in stories if not s.is_sponsor)

    chapters: list[ScriptedChapter] = [
        ScriptedChapter(
            idx=0,
            kind="intro",
            section=None,
            headline=None,
            summary_source=None,
            script_text=build_intro(edition, issue_date, n_stories),
            url=None,
            read_time=None,
        )
    ]

    prev_section: str | None = None
    for story in stories:
        # idx is the chapter's playback position, which is no longer the story's number.
        if story.is_sponsor:
            chapters.append(
                ScriptedChapter(
                    idx=len(chapters),
                    kind="sponsor",
                    section=None,
                    headline=story.headline,
                    summary_source=story.summary,
                    script_text=build_sponsor_script(story),
                    url=story.url,
                    read_time=story.read_time,
                )
            )
            continue

        glue: str | None = None
        if story.section != prev_section:
            lead = "First up" if prev_section is None else "Next up"
            glue = f"{lead} — {_spoken_section(story.section)}." if story.section else None
            prev_section = story.section
        chapters.append(
            ScriptedChapter(
                idx=len(chapters),
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
            idx=len(chapters),
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
