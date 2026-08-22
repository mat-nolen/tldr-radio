"""Kokoro TTS client — OpenAI-compatible API (validated in Spike #0).

Talks to `kokoro-fastapi-cpu` at KOKORO_URL. Per-chapter mp3, concurrency-limited
by the caller (spec §6 stage 5).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

import httpx
from mutagen.mp3 import MP3

log = logging.getLogger(__name__)

# Kokoro-FastAPI accepts any model string; "kokoro" is the canonical id.
DEFAULT_MODEL = "kokoro"

# Retry budget for one chapter. Kokoro occasionally drops a connection mid-body
# under sustained load — observed as `RemoteProtocolError: peer closed connection
# without sending complete message body`. Synthesis is a pure function of
# (text, voice), so a retry is always safe, and a chapter costs ~30 s against a
# worst case of 7 s of backoff. Without this, one blip fails the whole episode
# after most of its chapters are already written.
SYNTH_ATTEMPTS = 4
SYNTH_BACKOFF = 1.0  # seconds before the first retry; doubles each time


def _is_transient(exc: BaseException) -> bool:
    """True if retrying could plausibly succeed.

    Transport failures (connection dropped, timed out, protocol error) and the
    server saying "busy" or "broken" are worth another go. A 4xx means the
    request itself is wrong and will be just as wrong next time.
    """
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500
    return False


class KokoroClient:
    """Thin async client over Kokoro's OpenAI-compatible endpoints."""

    def __init__(self, base_url: str, synth_timeout: float = 600.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._synth_timeout = synth_timeout

    async def health(self) -> bool:
        """True if the voices endpoint answers 200 (server is up and warm)."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/v1/audio/voices")
                return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def list_voices(self) -> list[str]:
        """Return all available voice ids."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{self.base_url}/v1/audio/voices")
            resp.raise_for_status()
            data = resp.json()
        # Kokoro-FastAPI returns {"voices": [...]}; tolerate {"data": [...]} too.
        voices = data.get("voices") if isinstance(data, dict) else data
        if voices is None and isinstance(data, dict):
            voices = data.get("data", [])
        return list(voices or [])

    async def synthesize(
        self,
        text: str,
        voice: str,
        out_path: Path,
        response_format: str = "mp3",
        model: str = DEFAULT_MODEL,
    ) -> Path:
        """Synthesize `text` with `voice` and write the audio to `out_path`."""
        payload = {
            "model": model,
            "input": text,
            "voice": voice,
            "response_format": response_format,
        }
        for attempt in range(1, SYNTH_ATTEMPTS + 1):
            try:
                async with httpx.AsyncClient(timeout=self._synth_timeout) as client:
                    resp = await client.post(f"{self.base_url}/v1/audio/speech", json=payload)
                    resp.raise_for_status()
                    audio = resp.content
                break
            except Exception as exc:
                if attempt == SYNTH_ATTEMPTS or not _is_transient(exc):
                    raise
                delay = SYNTH_BACKOFF * 2 ** (attempt - 1)
                log.warning(
                    "synth attempt %d/%d for %s failed (%s: %s) — retrying in %.0fs",
                    attempt, SYNTH_ATTEMPTS, out_path.name, type(exc).__name__, exc, delay)
                await asyncio.sleep(delay)

        # Written only after a complete read, so a dropped connection can never
        # leave a truncated mp3 on disk for the concatenator to trip over.
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(audio)
        log.info("synthesized %d chars → %s (%d bytes)", len(text), out_path, len(audio))
        return out_path


async def synthesize_chapters(
    client: KokoroClient,
    chapters: Sequence,
    voice: str,
    out_dir: Path,
    concurrency: int = 4,
    on_done: Callable[[int, int], Awaitable[None]] | None = None,
    sponsor_voice: str | None = None,
) -> list[Path]:
    """Synthesize one mp3 per chapter (`<out_dir>/<idx>.mp3`), ≤`concurrency` at once.

    Each chapter object needs `.idx`, `.kind` and `.script_text`. `on_done(done, total)` is
    awaited after each chapter finishes, for progress reporting. Returns paths in chapter order.

    `sponsor_voice` reads sponsor chapters in a different voice — an audible ad disclosure, the
    way radio has always done it. Falls back to `voice` when unset, so a caller that doesn't
    care about sponsors keeps the old single-voice behaviour.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(concurrency)
    total = len(chapters)
    done = 0
    results: list[Path | None] = [None] * total

    def voice_for(chapter) -> str:
        if sponsor_voice and getattr(chapter, "kind", None) == "sponsor":
            return sponsor_voice
        return voice

    async def worker(position: int, chapter) -> None:
        nonlocal done
        async with sem:
            path = out_dir / f"{chapter.idx}.mp3"
            await client.synthesize(chapter.script_text, voice_for(chapter), path)
            results[position] = path
        done += 1
        if on_done is not None:
            await on_done(done, total)

    # A TaskGroup, not gather(): when one chapter fails for good the episode is
    # finished, and gather() leaves its siblings running — burning CPU on a
    # CPU-bound box and writing mp3s into a directory the retry is about to
    # reuse. The group cancels them.
    try:
        async with asyncio.TaskGroup() as tg:
            for i, ch in enumerate(chapters):
                tg.create_task(worker(i, ch))
    except BaseExceptionGroup as group:
        # Re-raise the underlying failure, not the wrapper. The worker records
        # `type(exc).__name__: exc` on the job, and prod reads that string —
        # "ExceptionGroup: unhandled errors in a TaskGroup" says nothing.
        raise _first_cause(group) from None

    return [p for p in results if p is not None]


def _first_cause(group: BaseExceptionGroup) -> BaseException:
    """The first real exception inside a (possibly nested) ExceptionGroup."""
    for exc in group.exceptions:
        if isinstance(exc, BaseExceptionGroup):
            return _first_cause(exc)
        if not isinstance(exc, asyncio.CancelledError):
            return exc
    return group


def mp3_duration(path: Path) -> float:
    """Audio length in seconds via mutagen (pure-Python; no system ffprobe needed)."""
    try:
        return float(MP3(path).info.length)
    except Exception:
        log.warning("could not read duration of %s", path)
        return 0.0
