"""A transient Kokoro failure must not destroy a whole episode.

Observed in the wild on 2026-08-17: the `ai` edition died on
`RemoteProtocolError: peer closed connection without sending complete message
body (incomplete chunked read)` after roughly twenty of its chapters were
already written. One dropped connection, one wasted 8–15 minute build, and on
prod that edition runs every night.

Synthesis is a pure function of (text, voice), so retrying is always safe. What
must NOT be retried is a request the server rejected on its merits — that fails
identically every time and would only delay the loud failure the spec asks for.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from app.pipeline import synth
from app.pipeline.synth import KokoroClient, synthesize_chapters


class Chapter:
    """The duck type `synthesize_chapters` needs."""

    def __init__(self, idx: int, text: str = "hello", kind: str = "story") -> None:
        self.idx, self.script_text, self.kind = idx, text, kind


def _client_returning(responses: list, monkeypatch) -> list[int]:
    """Point KokoroClient at a scripted sequence. Returns a mutable call counter.

    Each entry is either an exception to raise or an httpx.Response to return.
    """
    calls = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        i = calls[0]
        calls[0] += 1
        item = responses[min(i, len(responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    real_client = httpx.AsyncClient  # bind before patching, or the fake recurses

    def fake_async_client(**kwargs):
        kwargs.pop("transport", None)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(synth.httpx, "AsyncClient", fake_async_client)
    monkeypatch.setattr(synth, "SYNTH_BACKOFF", 0.0)  # keep the suite fast
    return calls


def _ok(body: bytes = b"\xff\xfb\x90\x00audio") -> httpx.Response:
    return httpx.Response(200, content=body)


def _drop() -> httpx.RemoteProtocolError:
    return httpx.RemoteProtocolError(
        "peer closed connection without sending complete message body")


# ---- the bug that was actually hit -----------------------------------------

def test_dropped_connection_is_retried_and_the_chapter_survives(tmp_path, monkeypatch):
    calls = _client_returning([_drop(), _drop(), _ok()], monkeypatch)
    out = tmp_path / "3.mp3"

    asyncio.run(KokoroClient("http://kokoro:8880").synthesize("hi", "af_heart", out))

    assert calls[0] == 3, "should have retried twice before succeeding"
    assert out.read_bytes() == b"\xff\xfb\x90\x00audio"


def test_a_timeout_is_retried_too(tmp_path, monkeypatch):
    calls = _client_returning([httpx.ReadTimeout("too slow"), _ok()], monkeypatch)
    asyncio.run(KokoroClient("http://k").synthesize("hi", "af_heart", tmp_path / "0.mp3"))
    assert calls[0] == 2


def test_a_500_is_retried(tmp_path, monkeypatch):
    calls = _client_returning([httpx.Response(500, text="boom"), _ok()], monkeypatch)
    asyncio.run(KokoroClient("http://k").synthesize("hi", "af_heart", tmp_path / "0.mp3"))
    assert calls[0] == 2


# ---- what must NOT be retried ----------------------------------------------

def test_a_bad_request_fails_immediately(tmp_path, monkeypatch):
    """A 400 means the request is wrong; retrying only delays the failure."""
    calls = _client_returning([httpx.Response(400, text="unknown voice")], monkeypatch)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(KokoroClient("http://k").synthesize("hi", "nope", tmp_path / "0.mp3"))

    assert calls[0] == 1, "a 4xx must not be retried"


def test_it_gives_up_and_fails_loudly(tmp_path, monkeypatch):
    """Retries buy time; they must not turn a real outage into silence."""
    calls = _client_returning([_drop()], monkeypatch)
    out = tmp_path / "0.mp3"

    with pytest.raises(httpx.RemoteProtocolError):
        asyncio.run(KokoroClient("http://k").synthesize("hi", "af_heart", out))

    assert calls[0] == synth.SYNTH_ATTEMPTS
    assert not out.exists(), "a failed synth must not leave a file behind"


def test_backoff_grows_between_attempts(tmp_path, monkeypatch):
    """Hammering a struggling server is how a blip becomes an outage."""
    _client_returning([_drop()], monkeypatch)
    monkeypatch.setattr(synth, "SYNTH_BACKOFF", 1.0)
    slept: list[float] = []

    async def fake_sleep(d: float) -> None:
        slept.append(d)

    monkeypatch.setattr(synth.asyncio, "sleep", fake_sleep)
    with pytest.raises(httpx.RemoteProtocolError):
        asyncio.run(KokoroClient("http://k").synthesize("hi", "af_heart", tmp_path / "0.mp3"))

    assert slept == [1.0, 2.0, 4.0]


# ---- the episode-level contract --------------------------------------------

def test_a_permanent_failure_cancels_the_other_chapters(tmp_path):
    """`gather()` does not cancel siblings when one task raises — it just stops
    waiting for them. They stay in the loop, keep burning CPU on an episode that
    is already dead, and keep writing mp3s into a directory the retry is about
    to reuse.

    The trap when testing this: `asyncio.run()` tears down leftover tasks when
    the loop closes, so an assertion made *after* the run cannot tell an
    orphaned task from a cancelled one. The check has to happen inside the same
    loop, after the failure has propagated — hence the sleep below, which is
    long enough for an orphan to finish and record itself.
    """
    finished: list[str] = []

    class SlowClient:
        async def synthesize(self, text: str, voice: str, out_path: Path) -> Path:
            if text == "boom":
                await asyncio.sleep(0)      # let the siblings actually start
                raise httpx.RemoteProtocolError("dropped")
            await asyncio.sleep(0.05)
            finished.append(text)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"x")
            return out_path

    chapters = [Chapter(0, "boom"), Chapter(1, "slow-a"), Chapter(2, "slow-b")]

    async def scenario() -> list[str]:
        with pytest.raises(httpx.RemoteProtocolError):
            await synthesize_chapters(
                SlowClient(), chapters, "af_heart", tmp_path, concurrency=3)
        await asyncio.sleep(0.2)            # an orphan would finish in here
        return finished

    assert asyncio.run(scenario()) == [], "siblings kept running after the episode died"


def test_the_original_error_reaches_the_job_not_an_exceptiongroup(tmp_path):
    """The worker records `type(exc).__name__: exc` and prod reads that string.

    A TaskGroup would normally surface `ExceptionGroup: unhandled errors in a
    TaskGroup`, which names neither the failure nor the chapter.
    """
    class Boom:
        async def synthesize(self, text: str, voice: str, out_path: Path) -> Path:
            raise httpx.RemoteProtocolError("peer closed connection")

    with pytest.raises(httpx.RemoteProtocolError) as caught:
        asyncio.run(synthesize_chapters(
            Boom(), [Chapter(0)], "af_heart", tmp_path, concurrency=1))

    assert "peer closed connection" in str(caught.value)
    assert not isinstance(caught.value, BaseExceptionGroup)
