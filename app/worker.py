"""Background asyncio job queue + lifecycle state machine (spec §"Job queue").

One job per edition: queued → fetching → parsing → scripting → synthesizing (n/m) →
ready | skipped | failed. Each transition is broadcast to SSE subscribers. Episodes + chapters
are persisted to SQLite before synthesis so the library can render immediately.

`skipped` means "tldr.tech has no issue for that date yet" (weekend, holiday, not posted yet).
It is a quiet, non-error outcome — distinct from `failed`, which always means something needs
looking at. Both are terminal and both can be re-queued via `retry`.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import logging
import shutil
from dataclasses import dataclass

from . import db
from .config import config
from .editions import name_for
from .pipeline.fetch import ARCHIVE_URL, NotPublishedError, fetch_archive
from .pipeline.parse import EmptyParseError, parse_edition, real_stories
from .pipeline.script import build_scripts
from .pipeline.synth import KokoroClient, mp3_duration, synthesize_chapters

log = logging.getLogger(__name__)


class JobStatus(enum.StrEnum):
    QUEUED = "queued"
    FETCHING = "fetching"
    PARSING = "parsing"
    SCRIPTING = "scripting"
    SYNTHESIZING = "synthesizing"
    READY = "ready"
    SKIPPED = "skipped"
    FAILED = "failed"


#: Statuses a job doesn't leave on its own — the auto-broadcast waits for one of these.
TERMINAL = frozenset({JobStatus.READY, JobStatus.SKIPPED, JobStatus.FAILED})


@dataclass
class Job:
    id: int
    edition: str
    issue_date: str
    voice: str
    # Captured at enqueue, like `voice` — a job builds the episode the settings described when
    # it was queued, so a mid-run toggle can't produce a half-sponsored episode.
    include_sponsors: bool = False
    sponsor_voice: str | None = None
    status: JobStatus = JobStatus.QUEUED
    synth_done: int = 0
    synth_total: int = 0
    error: str | None = None
    note: str | None = None  # non-error explanation (why a job was skipped)
    episode_id: int | None = None

    def as_event(self) -> dict:
        return {
            "id": self.id,
            "edition": self.edition,
            "issue_date": self.issue_date,
            "voice": self.voice,
            "include_sponsors": self.include_sponsors,
            "status": self.status.value,
            "progress": [self.synth_done, self.synth_total],
            "error": self.error,
            "note": self.note,
            "episode_id": self.episode_id,
        }


class JobQueue:
    """Single-worker asyncio queue that runs the pipeline and persists episodes."""

    def __init__(self, kokoro: KokoroClient) -> None:
        self._kokoro = kokoro
        self._queue: asyncio.Queue[Job] = asyncio.Queue()
        self._jobs: dict[int, Job] = {}
        self._subscribers: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None
        self._next_id = 1

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    # ---- queue / subscription ----

    def snapshot(self) -> list[dict]:
        return [job.as_event() for job in self._jobs.values()]

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def _emit(self, job: Job) -> None:
        for queue in list(self._subscribers):
            queue.put_nowait(job.as_event())

    async def enqueue(
        self,
        edition: str,
        issue_date: str,
        voice: str,
        include_sponsors: bool = False,
        sponsor_voice: str | None = None,
    ) -> Job:
        job = Job(
            id=self._next_id,
            edition=edition,
            issue_date=issue_date,
            voice=voice,
            include_sponsors=include_sponsors,
            sponsor_voice=sponsor_voice,
        )
        self._next_id += 1
        self._jobs[job.id] = job
        await self._queue.put(job)
        self._emit(job)
        return job

    async def retry(self, job_id: int) -> Job | None:
        """Re-queue a finished job in place (same id, same card) — failed or skipped."""
        job = self._jobs.get(job_id)
        if job is None or job.status not in (JobStatus.FAILED, JobStatus.SKIPPED):
            return None
        job.status = JobStatus.QUEUED
        job.error = job.note = None
        job.synth_done = job.synth_total = 0
        await self._queue.put(job)
        self._emit(job)
        return job

    # ---- worker loop ----

    async def _run(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                await self._process(job)
            except NotPublishedError as exc:
                # Not an error: no issue exists for that date (weekend/holiday/not posted yet).
                # The card only needs the headline; the redirect detail belongs in the log.
                log.info("job %d not published: %s", job.id, exc)
                self._skip(job, "Not published yet — tldr.tech has no issue for this date")
            except EmptyParseError as exc:
                self._fail(job, str(exc))
            except Exception as exc:
                log.exception("job %d failed", job.id)
                self._fail(job, f"{type(exc).__name__}: {exc}")
            finally:
                self._queue.task_done()

    def _set(self, job: Job, status: JobStatus) -> None:
        job.status = status
        self._emit(job)

    def _skip(self, job: Job, note: str) -> None:
        """Terminate a job quietly — nothing to fix, nothing to alert on."""
        job.status = JobStatus.SKIPPED
        job.note = note
        log.info("job %d skipped: %s", job.id, note)
        self._emit(job)

    def _fail(self, job: Job, message: str) -> None:
        job.status = JobStatus.FAILED
        job.error = message
        self._emit(job)
        if job.episode_id is not None:
            with db.connect(config.db_path) as conn:
                db.set_episode_status(conn, job.episode_id, "failed", message)

    async def _process(self, job: Job) -> None:
        with db.connect(config.db_path) as conn:
            existing = db.find_episode(conn, job.edition, job.issue_date)
        if existing is not None and existing["status"] == "ready":
            # A re-run of a job whose episode has since been produced (a stale card's Retry, or
            # an overnight re-check that raced a manual broadcast). Adopt it; don't rebuild it.
            job.episode_id = int(existing["id"])
            log.info("job %d: episode %d already ready — nothing to do", job.id, job.episode_id)
            self._set(job, JobStatus.READY)
            return

        self._set(job, JobStatus.FETCHING)
        html = await fetch_archive(job.edition, job.issue_date, config.cache_dir)

        self._set(job, JobStatus.PARSING)
        parsed = parse_edition(html)
        # The whole sponsor decision lives here: keep them and they become their own chapters
        # downstream, drop them and nothing below this line can tell the difference. It is a
        # build-time choice, so flipping the setting only affects the NEXT broadcast — episodes
        # already on disk keep the shape they were built with.
        stories = parsed if job.include_sponsors else real_stories(parsed)
        story_count = sum(1 for s in stories if not s.is_sponsor)

        self._set(job, JobStatus.SCRIPTING)
        chapters = build_scripts(job.edition, job.issue_date, stories)
        job.synth_total = len(chapters)

        # Persist episode + chapter rows before synth so the library renders immediately.
        title = f"{name_for(job.edition)} — {job.issue_date}"
        source_url = ARCHIVE_URL.format(edition=job.edition, date=job.issue_date)
        with db.connect(config.db_path) as conn:
            # A first attempt that died during synthesis left a row behind, and episodes has
            # UNIQUE (edition, issue_date) — inserting again would raise an IntegrityError that
            # masks the original failure. Clear the partial run so this one starts clean.
            stale = db.find_episode(conn, job.edition, job.issue_date)
            if stale is not None:
                log.info("job %d: discarding partial episode %d", job.id, stale["id"])
                db.delete_episode(conn, stale["id"])
                shutil.rmtree(config.audio_dir / str(stale["id"]), ignore_errors=True)
            episode_id = db.create_episode(
                conn, job.edition, job.issue_date, title, source_url, job.voice
            )
            for chapter in chapters:
                db.insert_chapter(conn, episode_id, chapter)
            db.set_episode_status(conn, episode_id, "synthesizing")
        job.episode_id = episode_id

        self._set(job, JobStatus.SYNTHESIZING)
        out_dir = config.audio_dir / str(episode_id)

        async def on_done(done: int, total: int) -> None:
            job.synth_done, job.synth_total = done, total
            self._emit(job)

        await synthesize_chapters(
            self._kokoro,
            chapters,
            job.voice,
            out_dir,
            config.max_concurrent_synth,
            on_done,
            sponsor_voice=job.sponsor_voice,
        )

        # Record per-chapter audio path + duration, then mark the episode ready.
        total_duration = 0.0
        with db.connect(config.db_path) as conn:
            for chapter in chapters:
                path = out_dir / f"{chapter.idx}.mp3"
                duration = mp3_duration(path)
                total_duration += duration
                db.set_chapter_audio(conn, episode_id, chapter.idx, str(path), duration)
            # story_count counts real stories only — a sponsor read is not a story, and the
            # library's "N stories" must keep matching the printed issue.
            db.set_episode_ready(conn, episode_id, story_count, total_duration)

        self._set(job, JobStatus.READY)
        log.info("job %d ready → episode %d (%.0fs)", job.id, episode_id, total_duration)
