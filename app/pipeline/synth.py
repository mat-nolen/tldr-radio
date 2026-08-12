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
        async with httpx.AsyncClient(timeout=self._synth_timeout) as client:
            resp = await client.post(f"{self.base_url}/v1/audio/speech", json=payload)
            resp.raise_for_status()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(resp.content)
        log.info("synthesized %d chars → %s (%d bytes)", len(text), out_path, len(resp.content))
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

    await asyncio.gather(*(worker(i, ch) for i, ch in enumerate(chapters)))
    return [p for p in results if p is not None]


def mp3_duration(path: Path) -> float:
    """Audio length in seconds via mutagen (pure-Python; no system ffprobe needed)."""
    try:
        return float(MP3(path).info.length)
    except Exception:
        log.warning("could not read duration of %s", path)
        return 0.0
