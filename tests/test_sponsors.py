"""Sponsor reads — chapters, numbering, the sponsor voice, and the build-time setting.

The product rule these pin down: a sponsor is a real, skippable chapter that never counts as a
story. Everything that reports "how many stories" — the intro line, `episodes.story_count`, the
"STORY n / m" position — must step over sponsors and keep matching the printed issue.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from app import db, main
from app.pipeline.parse import Story, parse_edition, real_stories
from app.pipeline.script import SPONSOR_LEAD, build_scripts
from app.pipeline.synth import synthesize_chapters

FIXTURES = Path(__file__).parent / "fixtures"

# Hand-counted from the 2026-07-22 fixtures (same source as tests/test_parse.py).
EXPECTED = {"tech": (15, 3), "ai": (17, 2), "infosec": (16, 2)}  # (real stories, sponsors)


def load(edition: str) -> str:
    return (FIXTURES / f"{edition}-2026-07-22.html").read_text(encoding="utf-8")


# ---- chapter structure ----------------------------------------------------

@pytest.mark.parametrize("edition,counts", EXPECTED.items())
def test_sponsors_become_their_own_chapters(edition: str, counts: tuple[int, int]):
    n_real, n_sponsor = counts
    chapters = build_scripts(edition, "2026-07-22", parse_edition(load(edition)))
    kinds = [c.kind for c in chapters]
    assert kinds.count("story") == n_real
    assert kinds.count("sponsor") == n_sponsor
    # intro + every item + outro, and playback order stays gapless.
    assert len(chapters) == n_real + n_sponsor + 2
    assert [c.idx for c in chapters] == list(range(len(chapters)))


@pytest.mark.parametrize("edition,counts", EXPECTED.items())
def test_dropping_sponsors_reproduces_the_old_shape(edition: str, counts: tuple[int, int]):
    """Sponsors off must be byte-for-byte the behaviour that shipped before they existed."""
    n_real, _ = counts
    parsed = parse_edition(load(edition))
    chapters = build_scripts(edition, "2026-07-22", real_stories(parsed))
    assert [c.kind for c in chapters] == ["intro"] + ["story"] * n_real + ["outro"]


def test_sponsors_keep_their_position_in_the_issue():
    """A sponsor is interstitial — it must land where the newsletter put it, not at the end."""
    parsed = parse_edition(load("tech"))
    chapters = build_scripts("tech", "2026-07-22", parsed)
    # Reading order of parsed items, ignoring intro/outro, must match the source exactly.
    body = [c.kind for c in chapters if c.kind in ("story", "sponsor")]
    assert body == ["sponsor" if s.is_sponsor else "story" for s in parsed]


def test_sponsors_do_not_count_as_stories():
    """The intro line and the story count both describe the printed issue, not the audio."""
    parsed = parse_edition(load("tech"))
    with_ads = build_scripts("tech", "2026-07-22", parsed)
    without = build_scripts("tech", "2026-07-22", real_stories(parsed))
    assert "15 stories" in with_ads[0].script_text
    assert with_ads[0].script_text == without[0].script_text


def test_sponsor_takes_no_section_and_no_glue():
    """Section glue announces a run of stories; an ad must not open or interrupt one."""
    chapters = build_scripts("tech", "2026-07-22", parse_edition(load("tech")))
    sponsors = [c for c in chapters if c.kind == "sponsor"]
    assert sponsors
    for c in sponsors:
        assert c.section is None
        assert not c.script_text.startswith(("First up", "Next up"))
    # The first *story* still opens the first section, even with an ad ahead of it.
    first_story = next(c for c in chapters if c.kind == "story")
    assert first_story.script_text.startswith("First up — ")


def test_sponsor_script_opens_with_a_spoken_disclosure():
    chapters = build_scripts("tech", "2026-07-22", parse_edition(load("tech")))
    for c in (c for c in chapters if c.kind == "sponsor"):
        assert c.script_text.startswith(SPONSOR_LEAD)
    # And a real story never claims to be one.
    for c in (c for c in chapters if c.kind == "story"):
        assert SPONSOR_LEAD not in c.script_text


def test_sponsor_keeps_its_headline_and_link():
    """The card and the read-more link are what make the ad worth anything to the sponsor."""
    chapters = build_scripts("tech", "2026-07-22", parse_edition(load("tech")))
    sponsor = next(c for c in chapters if c.kind == "sponsor")
    assert sponsor.headline
    assert sponsor.url


# ---- the sponsor voice ----------------------------------------------------

class RecordingClient:
    """Stand-in for KokoroClient that records the voice asked for, per chapter."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []  # (voice, text)

    async def synthesize(self, text: str, voice: str, out_path: Path) -> Path:
        self.calls.append((voice, text))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"")
        return out_path


def _synth(chapters, tmp_path, **kwargs) -> RecordingClient:
    client = RecordingClient()
    asyncio.run(synthesize_chapters(client, chapters, "af_heart", tmp_path, 1, None, **kwargs))
    return client


def test_sponsor_chapters_are_read_in_the_sponsor_voice(tmp_path):
    chapters = build_scripts("tech", "2026-07-22", parse_edition(load("tech")))
    client = _synth(chapters, tmp_path, sponsor_voice="bm_george")

    # Keyed on the script text, not call order: chapters are synthesized through a semaphore
    # and gather(), so the order calls land in is not a guarantee worth asserting on.
    voice_of = {text: voice for voice, text in client.calls}
    assert len(voice_of) == len(chapters)
    for c in chapters:
        expected = "bm_george" if c.kind == "sponsor" else "af_heart"
        assert voice_of[c.script_text] == expected, f"{c.kind} chapter {c.idx} used the wrong voice"


def test_without_a_sponsor_voice_everything_uses_the_main_voice(tmp_path):
    """An unset sponsor voice must not silently drop back to some default — it means 'same'."""
    chapters = build_scripts("tech", "2026-07-22", parse_edition(load("tech")))
    client = _synth(chapters, tmp_path)
    assert {v for v, _ in client.calls} == {"af_heart"}


# ---- the setting ----------------------------------------------------------

@pytest.fixture
def settings_db(tmp_path, monkeypatch):
    db_path = tmp_path / "episodes.db"
    db.init_db(db_path)

    def configure(**overrides):
        monkeypatch.setattr(
            main, "config", replace(main.config, data_dir=tmp_path, **overrides)
        )
        return db_path

    return configure


def test_sponsors_ship_enabled_by_default(settings_db):
    """A fresh install plays the newsletter as published — the honest default."""
    settings_db()
    settings = main._settings()
    assert settings["include_sponsors"] is True
    assert settings["sponsor_voice"] == "bm_george"


def test_env_seeds_the_first_read_only(settings_db):
    db_path = settings_db(include_sponsors=False, sponsor_voice="bm_lewis")
    assert main._settings()["include_sponsors"] is False

    # Once the UI saves, the table wins and the env var is irrelevant — no redeploy to change it.
    asyncio.run(main.put_settings(main.SettingsRequest(include_sponsors=True)))
    assert main._settings()["include_sponsors"] is True
    with db.connect(db_path) as conn:
        assert db.get_setting(conn, "include_sponsors") == "1"


def test_the_toggle_round_trips_both_ways(settings_db):
    settings_db()
    for wanted in (False, True, False):
        result = asyncio.run(main.put_settings(main.SettingsRequest(include_sponsors=wanted)))
        assert result["include_sponsors"] is wanted
        assert main._settings()["include_sponsors"] is wanted


def test_saving_the_voice_leaves_the_toggle_alone(settings_db):
    """Partial payloads are the norm here — one panel must not clear the other's setting."""
    settings_db()
    asyncio.run(main.put_settings(main.SettingsRequest(include_sponsors=False)))
    asyncio.run(main.put_settings(main.SettingsRequest(sponsor_voice="am_michael")))
    settings = main._settings()
    assert settings["include_sponsors"] is False
    assert settings["sponsor_voice"] == "am_michael"


def test_story_count_excludes_sponsors():
    """episodes.story_count feeds the Library's 'N stories' — it counts the issue, not the audio."""
    parsed = parse_edition(load("tech"))
    assert sum(1 for s in parsed if not s.is_sponsor) == 15
    assert len(parsed) == 18  # 15 stories + 3 ads: the number that must NOT be reported


def test_a_page_of_nothing_but_ads_is_not_a_healthy_issue():
    """The parser's health signal counts real stories, so sponsors can't pad a broken layout.

    `parse_edition` raises EmptyParseError on zero non-sponsor stories (covered in test_parse);
    this pins the property that guard depends on — `real_stories` never counts an ad.
    """
    ads = [Story("X", f"Buy this {i}", "Now", None, None, is_sponsor=True) for i in range(5)]
    assert real_stories(ads) == []
    # And a page that is all ads produces an episode claiming zero stories, never five.
    chapters = build_scripts("tech", "2026-07-22", ads)
    assert "0 stories" in chapters[0].script_text
    assert not [c for c in chapters if c.kind == "story"]
