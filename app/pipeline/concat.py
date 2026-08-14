"""Join an episode's chapter mp3s into one file — byte-append, no re-encode, no ffmpeg.

Every chapter comes out of Kokoro at identical settings (24 kHz mono, MPEG-2 Layer III,
128 kbps CBR), so the frames are already compatible and the file can simply be concatenated.
Verified in the download spike against three independent decoders:

  - `mutagen`      387.360 s
  - CoreAudio      387.360 s  (`afinfo` — the parser iOS and macOS audio apps use)
  - WebKit         387.36 s, seeks to 10 s / 200 s / 380 s all landing exactly

Two properties of Kokoro's output make this safe, and both are worth stating because the
technique stops working if either changes:

  1. **No Xing/Info header.** That header carries a frame count for the whole file. Had one been
     present, every decoder would have read chapter 0's duration and reported it for the join —
     the classic "concatenated mp3 shows the wrong length and seeks into nowhere" failure.
  2. **Constant bitrate.** With no Xing header a decoder derives position from byte offset, which
     is only accurate for CBR. A VBR source would still play but would seek inaccurately.

`assert_concatenable` checks both on every build, so a Kokoro upgrade that changed them fails
loudly here instead of silently shipping a broken file.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from mutagen.id3 import COMM, TALB, TCON, TDRC, TIT2, TPE1, TPE2
from mutagen.mp3 import MP3

log = logging.getLogger(__name__)


class NotConcatenableError(Exception):
    """Raised when chapter audio can't be safely joined by byte-append."""


@dataclass(frozen=True)
class SourceInfo:
    """What a decoder will make of one chapter file."""

    path: Path
    length: float
    bitrate: int
    sample_rate: int
    channels: int
    has_xing: bool


def id3v2_size(buf: bytes) -> int:
    """Bytes occupied by a leading ID3v2 tag, or 0 if there isn't one.

    The size field is 'synchsafe' — 7 usable bits per byte, so the high bit can never create a
    false frame sync. Kokoro writes a 45-byte tag holding only TSSE (encoder name).
    """
    if len(buf) < 10 or buf[:3] != b"ID3":
        return 0
    size = 0
    for byte in buf[6:10]:
        size = (size << 7) | (byte & 0x7F)
    footer = 10 if (buf[5] & 0x10) else 0  # ID3v2.4 may repeat the header at the end
    return 10 + size + footer


def inspect(path: Path) -> SourceInfo:
    info = MP3(path).info
    head = path.read_bytes()[:4096]
    return SourceInfo(
        path=path,
        length=float(info.length),
        bitrate=int(info.bitrate),
        sample_rate=int(info.sample_rate),
        channels=int(info.channels),
        # Either spelling marks the header; 'Info' is the CBR form of 'Xing'.
        has_xing=(b"Xing" in head or b"Info" in head),
    )


def assert_concatenable(sources: list[SourceInfo]) -> None:
    """Fail loudly if byte-append would produce a file that plays or seeks wrongly."""
    if not sources:
        raise NotConcatenableError("no chapter audio to join")
    first = sources[0]
    for s in sources:
        if s.has_xing:
            raise NotConcatenableError(
                f"{s.path.name} carries a Xing/Info header — a join would report only the "
                "first chapter's duration. Re-encode with ffmpeg instead."
            )
        shape = (s.sample_rate, s.channels, s.bitrate)
        if shape != (first.sample_rate, first.channels, first.bitrate):
            raise NotConcatenableError(
                f"{s.path.name} is {s.sample_rate}Hz/{s.channels}ch/{s.bitrate}bps but "
                f"{first.path.name} is {first.sample_rate}Hz/{first.channels}ch/"
                f"{first.bitrate}bps — mixed formats cannot be byte-appended."
            )


def concat_mp3s(paths: list[Path], out_path: Path) -> float:
    """Join `paths` in order into `out_path`. Returns the expected duration in seconds.

    The first file keeps its ID3v2 tag (it becomes the combined file's tag); every later tag is
    stripped, because an ID3 header sitting mid-stream is data a decoder counts as audio. Left
    in, 16 of them drifted a 6.5-minute episode by 45 ms — small, but it means the reported
    length is never quite the real one, and it compounds with chapter count.
    """
    sources = [inspect(p) for p in paths]
    assert_concatenable(sources)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".mp3.part")
    # Written to a temp name and moved into place so a crash mid-write can't leave a truncated
    # file that later looks like a valid cache hit.
    try:
        with tmp.open("wb") as out:
            for i, path in enumerate(paths):
                data = path.read_bytes()
                out.write(data if i == 0 else data[id3v2_size(data) :])
        tmp.replace(out_path)
    except BaseException:
        # Leave nothing behind. The partial file is unusable, and on a box that builds every
        # night a stray .part per failure is disk that never comes back on its own.
        tmp.unlink(missing_ok=True)
        raise

    expected = sum(s.length for s in sources)
    log.info(
        "joined %d chapters -> %s (%.1f s, %.1f MB)",
        len(paths), out_path, expected, out_path.stat().st_size / 1e6,
    )
    return expected


def write_tags(
    path: Path,
    *,
    edition_name: str,
    issue_date: str,
    story_count: int,
    comment: str | None = None,
) -> None:
    """Tag the combined file so a player shelves it instead of showing a filename.

    The mapping is chosen for **audiobook** apps, which shelve by album and sort within it:

      TALB (album)  = "TLDR Tech"          → every Tech episode collects on one shelf
      TIT2 (title)  = "TLDR Tech — 2026-08-10"  → the episode, identifiable on its own
      TPE1 (artist) = "TLDR"               → the author field most apps display
      TDRC (date)   = the issue date       → sorts the shelf chronologically

    A podcast app would want these subtly differently (album = show, artist = publisher), but
    the two agree closely enough that one tagging serves both.
    """
    audio = MP3(path)
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    tags.setall("TALB", [TALB(encoding=3, text=[edition_name])])
    tags.setall("TIT2", [TIT2(encoding=3, text=[f"{edition_name} — {issue_date}"])])
    tags.setall("TPE1", [TPE1(encoding=3, text=["TLDR"])])
    tags.setall("TPE2", [TPE2(encoding=3, text=["TLDR Radio"])])
    tags.setall("TDRC", [TDRC(encoding=3, text=[issue_date])])
    tags.setall("TCON", [TCON(encoding=3, text=["Speech"])])
    body = comment or f"{story_count} stories. Read by TLDR Radio."
    tags.setall("COMM", [COMM(encoding=3, lang="eng", desc="", text=[body])])
    # v2.3 rather than the default v2.4: older audiobook players still parse it more reliably,
    # and nothing here needs a v2.4-only frame.
    audio.save(v2_version=3)
