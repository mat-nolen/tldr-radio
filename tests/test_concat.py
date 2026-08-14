"""Episode download — joining chapter mp3s into one tagged file, without ffmpeg.

The properties pinned here are the ones the whole technique rests on. Byte-appending mp3s is
only safe because Kokoro emits constant-bitrate frames with no Xing/Info header; if either ever
changes, the join must fail loudly rather than ship a file that reports the wrong length and
seeks into nowhere. `assert_concatenable` is that guard, and most of this file tests it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mutagen.id3 import ID3
from mutagen.mp3 import MP3, HeaderNotFoundError

from app.pipeline.concat import (
    NotConcatenableError,
    SourceInfo,
    assert_concatenable,
    concat_mp3s,
    id3v2_size,
    write_tags,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _src(name="a.mp3", *, xing=False, rate=24000, ch=1, bitrate=128000, length=1.0) -> SourceInfo:
    return SourceInfo(Path(name), length, bitrate, rate, ch, xing)


# ---- ID3v2 header sizing --------------------------------------------------

def test_no_tag_is_zero():
    assert id3v2_size(b"\xff\xfb\x90\x00" + b"\x00" * 100) == 0
    assert id3v2_size(b"") == 0
    assert id3v2_size(b"ID3") == 0  # too short to carry a size field


def test_synchsafe_size_is_seven_bits_per_byte():
    """0x00 0x00 0x02 0x01 is 257, not 513 — the high bit of each byte is never used."""
    header = b"ID3\x03\x00\x00" + bytes([0x00, 0x00, 0x02, 0x01])
    assert id3v2_size(header) == 10 + 257


def test_footer_flag_adds_ten_bytes():
    """ID3v2.4 may repeat the header as a footer; missing it would leave 10 bytes mid-stream."""
    without = b"ID3\x04\x00\x00" + bytes([0, 0, 0, 32])
    with_footer = b"ID3\x04\x00\x10" + bytes([0, 0, 0, 32])
    assert id3v2_size(with_footer) - id3v2_size(without) == 10


def test_real_kokoro_chapter_tag_is_measured():
    ch = next(FIXTURES.glob("chapter-*.mp3"), None)
    if ch is None:
        pytest.skip("no chapter fixture available")
    assert id3v2_size(ch.read_bytes()) > 0


# ---- the safety guard -----------------------------------------------------

def test_empty_input_is_rejected():
    with pytest.raises(NotConcatenableError, match="no chapter audio"):
        assert_concatenable([])


def test_a_xing_header_is_refused():
    """The failure this guard exists for: a Xing header makes the join report chapter 0's length."""
    with pytest.raises(NotConcatenableError, match="Xing/Info"):
        assert_concatenable([_src("0.mp3"), _src("1.mp3", xing=True)])


@pytest.mark.parametrize(
    "odd",
    [
        {"rate": 44100},
        {"ch": 2},
        {"bitrate": 192000},
    ],
    ids=["sample-rate", "channels", "bitrate"],
)
def test_mixed_formats_are_refused(odd):
    with pytest.raises(NotConcatenableError, match="cannot be byte-appended"):
        assert_concatenable([_src("0.mp3"), _src("1.mp3", **odd)])


def test_uniform_cbr_without_xing_is_accepted():
    assert_concatenable([_src("0.mp3"), _src("1.mp3"), _src("2.mp3")])  # does not raise


# ---- joining --------------------------------------------------------------

@pytest.fixture
def chapters() -> list[Path]:
    found = sorted(FIXTURES.glob("chapter-*.mp3"))
    if len(found) < 2:
        pytest.skip("needs at least 2 chapter fixtures")
    return found


def test_join_length_is_the_sum_of_the_parts(chapters, tmp_path):
    out = tmp_path / "episode.mp3"
    expected = concat_mp3s(chapters, out)
    assert expected == pytest.approx(sum(MP3(p).info.length for p in chapters))
    # The real check: what a decoder reads back, not what we predicted.
    assert MP3(out).info.length == pytest.approx(expected, abs=0.05)


def test_only_the_first_tag_survives(chapters, tmp_path):
    """Every later ID3 header is data a decoder counts as audio — 16 of them drifted 45 ms."""
    out = tmp_path / "episode.mp3"
    concat_mp3s(chapters, out)
    stripped = sum(id3v2_size(p.read_bytes()) for p in chapters[1:])
    assert out.stat().st_size == sum(p.stat().st_size for p in chapters) - stripped


def test_join_is_byte_identical_to_manual_append(chapters, tmp_path):
    """No re-encode: the audio bytes must survive untouched, or it isn't lossless."""
    out = tmp_path / "episode.mp3"
    concat_mp3s(chapters, out)
    manual = chapters[0].read_bytes() + b"".join(
        p.read_bytes()[id3v2_size(p.read_bytes()):] for p in chapters[1:]
    )
    assert out.read_bytes() == manual


def test_unreadable_input_fails_before_writing_anything(chapters, tmp_path):
    """Every source is inspected up front, so a bad file is caught before the output exists."""
    out = tmp_path / "episode.mp3"
    bad = tmp_path / "bogus.mp3"
    bad.write_bytes(b"not an mp3 at all")
    with pytest.raises(HeaderNotFoundError):
        concat_mp3s([chapters[0], bad], out)
    assert not out.exists()


def test_a_crash_mid_write_leaves_no_usable_file(chapters, tmp_path, monkeypatch):
    """The reason for the temp-name-then-rename: a truncated file would be a poisoned cache.

    `download_episode` treats "the file exists" as "it is complete", so a half-written episode
    would be served forever without ever retrying. Fail *during* the write, not at validation,
    to exercise the rename rather than the up-front inspection.
    """
    out = tmp_path / "episode.mp3"
    real_read = Path.read_bytes
    calls = {"n": 0}

    def flaky(self, *a, **kw):
        # Let inspection read freely; blow up only once the join is underway.
        if self.suffix == ".mp3" and self.parent == chapters[0].parent:
            calls["n"] += 1
            if calls["n"] > len(chapters) + 1:
                raise OSError("disk went away mid-write")
        return real_read(self, *a, **kw)

    monkeypatch.setattr(Path, "read_bytes", flaky)
    with pytest.raises(OSError, match="disk went away"):
        concat_mp3s(chapters, out)
    assert not out.exists(), "a truncated file would be served forever as a cache hit"
    assert not list(tmp_path.glob("*.part")), "temp file left behind"


def test_single_chapter_joins_cleanly(chapters, tmp_path):
    out = tmp_path / "episode.mp3"
    concat_mp3s([chapters[0]], out)
    assert out.read_bytes() == chapters[0].read_bytes()


# ---- tagging --------------------------------------------------------------

def test_tags_are_written_for_an_audiobook_shelf(chapters, tmp_path):
    out = tmp_path / "episode.mp3"
    concat_mp3s(chapters, out)
    write_tags(out, edition_name="TLDR Tech", issue_date="2026-08-10", story_count=13)
    tags = ID3(out)
    # Album groups the shelf, title identifies the episode on it.
    assert tags["TALB"].text[0] == "TLDR Tech"
    assert tags["TIT2"].text[0] == "TLDR Tech — 2026-08-10"
    assert tags["TPE1"].text[0] == "TLDR"
    assert str(tags["TDRC"].text[0]) == "2026-08-10"
    assert "13 stories" in tags.getall("COMM")[0].text[0]


def test_tagging_does_not_change_the_audio_length(chapters, tmp_path):
    """Tags are metadata; writing them must not alter what a player reports."""
    out = tmp_path / "episode.mp3"
    before = concat_mp3s(chapters, out)
    write_tags(out, edition_name="TLDR Tech", issue_date="2026-08-10", story_count=13)
    assert MP3(out).info.length == pytest.approx(before, abs=0.05)


# ---- the download route ---------------------------------------------------

@pytest.fixture
def episode_env(tmp_path, monkeypatch, chapters):
    """A ready episode on disk, with main pointed at a throwaway data dir."""
    from dataclasses import replace as dc_replace

    from app import db, main

    db_path = tmp_path / "episodes.db"
    db.init_db(db_path)
    monkeypatch.setattr(main, "config", dc_replace(main.config, data_dir=tmp_path))

    def build(status="ready", *, with_audio=True, edition="tech", date="2026-08-10"):
        with db.connect(db_path) as conn:
            ep_id = db.create_episode(
                conn, edition, date, f"TLDR Tech — {date}", "https://example.invalid", "af_heart"
            )
            for i in range(len(chapters)):
                kind = "intro" if i == 0 else "story"
                db.insert_chapter(conn, ep_id, type("C", (), {
                    "idx": i, "kind": kind, "section": None, "headline": None,
                    "summary_source": None, "script_text": "x", "url": None, "read_time": None,
                })())
            db.set_episode_ready(conn, ep_id, len(chapters), 12.0)
            if status != "ready":
                db.set_episode_status(conn, ep_id, status)
        if with_audio:
            d = tmp_path / "audio" / str(ep_id)
            d.mkdir(parents=True)
            for i, src in enumerate(chapters):
                (d / f"{i}.mp3").write_bytes(src.read_bytes())
        return ep_id

    return build


def _download(ep_id):
    import asyncio

    from app import main

    return asyncio.run(main.download_episode(ep_id))


def test_download_names_the_file_for_the_issue(episode_env):
    ep_id = episode_env()
    resp = _download(ep_id)
    assert resp.filename == "tldr-tech-2026-08-10.mp3"
    assert 'attachment; filename="tldr-tech-2026-08-10.mp3"' in resp.headers["content-disposition"]


def test_download_builds_then_reuses_the_cached_file(episode_env, tmp_path):
    ep_id = episode_env()
    combined = tmp_path / "audio" / str(ep_id) / "episode.mp3"
    assert not combined.exists()
    _download(ep_id)
    assert combined.exists()
    stamp = combined.stat().st_mtime_ns
    _download(ep_id)
    assert combined.stat().st_mtime_ns == stamp, "second request rebuilt instead of serving cache"


def test_download_lands_beside_its_chapters_so_cleanup_is_free(episode_env, tmp_path):
    """Retention and delete both rmtree the episode's audio dir — the cache must live there."""
    ep_id = episode_env()
    _download(ep_id)
    assert (tmp_path / "audio" / str(ep_id) / "episode.mp3").exists()


def test_download_is_the_length_of_the_whole_episode(episode_env, tmp_path, chapters):
    ep_id = episode_env()
    _download(ep_id)
    combined = tmp_path / "audio" / str(ep_id) / "episode.mp3"
    assert MP3(combined).info.length == pytest.approx(
        sum(MP3(p).info.length for p in chapters), abs=0.05
    )


def test_download_is_tagged(episode_env, tmp_path):
    ep_id = episode_env()
    _download(ep_id)
    tags = ID3(tmp_path / "audio" / str(ep_id) / "episode.mp3")
    assert tags["TALB"].text[0] == "TLDR Tech"
    assert "2026-08-10" in tags["TIT2"].text[0]


def test_unknown_episode_is_404(episode_env):
    from fastapi import HTTPException

    episode_env()
    with pytest.raises(HTTPException) as exc:
        _download(9999)
    assert exc.value.status_code == 404


def test_an_unfinished_episode_is_refused(episode_env):
    """Better a clear 409 than a download button that hands back a truncated file."""
    from fastapi import HTTPException

    ep_id = episode_env(status="synthesizing")
    with pytest.raises(HTTPException) as exc:
        _download(ep_id)
    assert exc.value.status_code == 409


def test_missing_chapter_audio_is_refused(episode_env):
    from fastapi import HTTPException

    ep_id = episode_env(with_audio=False)
    with pytest.raises(HTTPException) as exc:
        _download(ep_id)
    assert exc.value.status_code == 409
    assert "incomplete" in exc.value.detail
