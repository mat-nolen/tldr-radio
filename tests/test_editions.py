"""Edition catalog + the Desk/Auto settings that decide which newsletters the app runs.

The settings tests use a real temp SQLite file rather than stubs: the seeding rule (env seeds
the first read, the table wins afterwards) only exists in the interaction between
`db.get_setting`'s default and `_settings()`, so stubbing the DB would test nothing.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from app import db, editions, main


def test_catalog_covers_the_published_newsletters():
    # 14 newsletters listed on tldr.tech (verified 2026-08-06); slugs are the archive URL path.
    assert len(editions.EDITIONS) == 14
    assert set(editions.DEFAULT_EDITIONS) <= set(editions.EDITIONS)
    for slug, edition in editions.EDITIONS.items():
        assert slug == edition.slug
        assert slug == slug.lower().strip()
        assert edition.name.startswith("TLDR ")
        # Two different jobs: the tagline is the running order, the description is who it's for.
        assert edition.tagline
        assert edition.description
        assert edition.tagline != edition.description


def test_the_original_three_keep_their_names():
    # These strings are spoken in every intro — a rename would change the audio.
    assert editions.EDITION_NAMES["tech"] == "TLDR Tech"
    assert editions.EDITION_NAMES["ai"] == "TLDR AI"
    assert editions.EDITION_NAMES["infosec"] == "TLDR Infosec"


def test_name_for_falls_back_for_an_unknown_slug():
    assert editions.name_for("marketing") == "TLDR Marketing"
    assert editions.name_for("quantum") == "TLDR Quantum"


def test_known_filters_dedupes_and_orders():
    assert editions.known(["ai", "nope", "tech", "ai"]) == ("tech", "ai")
    assert editions.known([]) == ()
    assert editions.known(["definitely-not-real"]) == ()
    # Catalog order, not the caller's order.
    assert editions.known(["hardware", "tech"]) == ("tech", "hardware")


# --------------------------------------------------------------------------- settings
@pytest.fixture
def settings_db(tmp_path, monkeypatch):
    """Point main at a fresh DB, and let a test set the AUTO_BROADCAST_EDITIONS env default."""
    db_path = tmp_path / "episodes.db"
    db.init_db(db_path)

    def configure(env_editions: tuple[str, ...] = ("tech", "ai", "infosec")):
        monkeypatch.setattr(
            main,
            "config",
            replace(main.config, data_dir=tmp_path, auto_broadcast_editions=env_editions),
        )
        return db_path

    return configure


def test_defaults_match_what_the_app_shipped_with(settings_db):
    settings_db()
    settings = main._settings()
    assert settings["desk_editions"] == ["tech", "ai", "infosec"]
    assert settings["auto_editions"] == ["tech", "ai", "infosec"]


def test_env_narrows_the_night_run_without_emptying_the_desk(settings_db):
    """AUTO_BROADCAST_EDITIONS used to be the only list; it must not hide the other stations."""
    settings_db(("tech",))
    settings = main._settings()
    assert settings["auto_editions"] == ["tech"]
    assert settings["desk_editions"] == ["tech", "ai", "infosec"]


def test_env_seeds_only_the_first_read_then_the_table_wins(settings_db):
    db_path = settings_db(("tech",))
    with db.connect(db_path) as conn:
        db.set_setting(conn, "auto_editions", "ai,marketing")
    assert main._settings()["auto_editions"] == ["ai", "marketing"]


def test_auto_implies_desk_on_read(settings_db):
    """A slug can be saved into auto alone; the Desk must still offer it."""
    db_path = settings_db()
    with db.connect(db_path) as conn:
        db.set_setting(conn, "desk_editions", "tech")
        db.set_setting(conn, "auto_editions", "tech,crypto")
    settings = main._settings()
    assert settings["auto_editions"] == ["tech", "crypto"]
    assert settings["desk_editions"] == ["tech", "crypto"]


def test_unknown_slugs_are_dropped_not_served(settings_db):
    db_path = settings_db()
    with db.connect(db_path) as conn:
        db.set_setting(conn, "desk_editions", "tech,nonsense,dev")
        db.set_setting(conn, "auto_editions", "nonsense")
    settings = main._settings()
    assert settings["desk_editions"] == ["tech", "dev"]
    assert settings["auto_editions"] == []


def test_an_explicitly_empty_list_is_respected(settings_db):
    """Unticking everything must stay unticked — not silently fall back to the defaults."""
    db_path = settings_db()
    with db.connect(db_path) as conn:
        db.set_setting(conn, "desk_editions", "")
        db.set_setting(conn, "auto_editions", "")
    settings = main._settings()
    assert settings["desk_editions"] == []
    assert settings["auto_editions"] == []


# --------------------------------------------------------------------------- routes
def test_put_settings_saves_both_lists_and_drops_junk(settings_db):
    settings_db()
    saved = asyncio.run(
        main.put_settings(
            main.SettingsRequest(desk_editions=["dev", "bogus", "tech"], auto_editions=["dev"])
        )
    )
    assert saved["desk_editions"] == ["tech", "dev"]
    assert saved["auto_editions"] == ["dev"]
    # Survives a reload — this is the whole point of storing it rather than reading the env.
    assert main._settings()["desk_editions"] == ["tech", "dev"]


def test_editions_route_reports_desk_auto_and_an_estimate(settings_db):
    db_path = settings_db()
    asyncio.run(
        main.put_settings(
            main.SettingsRequest(desk_editions=["tech", "dev"], auto_editions=["dev"])
        )
    )
    with db.connect(db_path) as conn:
        for edition, seconds in (("tech", 600.0), ("dev", 900.0)):
            episode_id = db.create_episode(conn, edition, "2026-08-06", "t", "u", "af_heart")
            conn.execute(
                "UPDATE episodes SET status='ready', duration_seconds=? WHERE id=?",
                (seconds, episode_id),
            )
        conn.commit()

    payload = asyncio.run(main.list_editions())
    assert len(payload["editions"]) == 14
    by_slug = {e["slug"]: e for e in payload["editions"]}
    assert by_slug["dev"]["desk"] and by_slug["dev"]["auto"]
    assert by_slug["tech"]["desk"] and not by_slug["tech"]["auto"]
    assert not by_slug["crypto"]["desk"]
    assert by_slug["dev"]["name"] == "TLDR Dev"
    # Only `dev` runs overnight, and it averages 900s.
    assert payload["auto_audio_seconds"] == pytest.approx(900.0)


def test_an_edition_that_has_never_run_borrows_the_average(settings_db):
    db_path = settings_db()
    asyncio.run(main.put_settings(main.SettingsRequest(auto_editions=["tech", "crypto"])))
    with db.connect(db_path) as conn:
        episode_id = db.create_episode(conn, "tech", "2026-08-06", "t", "u", "af_heart")
        conn.execute(
            "UPDATE episodes SET status='ready', duration_seconds=600 WHERE id=?", (episode_id,)
        )
        conn.commit()
    # tech's own 600s, plus 600s borrowed for crypto, which has never been broadcast.
    assert asyncio.run(main.list_editions())["auto_audio_seconds"] == pytest.approx(1200.0)


def test_estimate_is_absent_rather_than_invented_on_a_fresh_install(settings_db):
    settings_db()
    assert asyncio.run(main.list_editions())["auto_audio_seconds"] is None


def test_jobs_route_refuses_an_unknown_slug(settings_db, monkeypatch):
    """An unknown slug would fetch a URL tldr.tech never serves and look like 'not published'."""
    settings_db()
    enqueued: list[str] = []

    class FakeJobs:
        async def enqueue(
            self, edition, issue_date, voice, include_sponsors=False, sponsor_voice=None
        ):
            enqueued.append(edition)
            return main.Job(id=1, edition=edition, issue_date=issue_date, voice=voice)

    monkeypatch.setattr(main, "jobs", FakeJobs())
    result = asyncio.run(
        main.create_jobs(main.JobRequest(editions=["dev", "totally-made-up"], date="2026-08-06"))
    )
    assert enqueued == ["dev"]
    assert result["rejected"] == ["totally-made-up"]
    assert [j["edition"] for j in result["created"]] == ["dev"]
