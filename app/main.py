"""FastAPI application — routes + SSE.

Phase 4: job queue (one per edition) with live SSE progress, episode/chapter persistence,
Range-capable audio serving, playback resume, settings, and voice auditions. The Broadcast
Desk UI (static files) is wired in Phase 4b.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import random
import re
import shutil
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, editions, plex, retention
from .config import APP_ROOT, config
from .pipeline.concat import NotConcatenableError, concat_mp3s, write_tags
from .pipeline.fetch import fetch_archive
from .pipeline.parse import parse_edition, real_stories
from .pipeline.script import build_scripts
from .pipeline.synth import KokoroClient, synthesize_chapters
from .worker import TERMINAL, Job, JobQueue, JobStatus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("tldr_radio")

kokoro = KokoroClient(config.kokoro_url)
jobs = JobQueue(kokoro)

# Outcome of the most recent overnight auto-broadcast (surfaced at /api/auto-broadcast/status).
last_auto_broadcast: dict | None = None

AUDITION_LINE = "You're listening to TLDR Radio. Here are today's top stories."
_VOICE_RE = re.compile(r"^[A-Za-z0-9_]+$")

# Re-check cadence for an edition that isn't published yet (jittered — never on the dot).
RETRY_INTERVAL_SECONDS = 25 * 60
RETRY_JITTER_SECONDS = 5 * 60

# Gap between the back-to-back fetches in /api/parser/health (see that route).
PARSER_HEALTH_DELAY_SECONDS = 1.5


class DataDirNotWritableError(RuntimeError):
    """`/data` cannot be written by the account the app is running as."""


def _probe_new_entry(directory: Path) -> None:
    """Raise OSError if a new entry cannot be created inside `directory`."""
    probe = directory / f".write-probe-{os.getpid()}"
    probe.touch()
    probe.unlink()


def ensure_data_dir_writable(data_dir: Path) -> None:
    """Fail at startup, loudly, if the data directory cannot be written.

    The container runs as a non-root user (see the Dockerfile) while `/data` is a host bind mount
    whose ownership the image cannot control. When the two disagree every write fails — the
    database, the mp3s, the page cache — and without this check the first symptom would be a
    broadcast dying part-way through, hours later, with a stack trace about sqlite.

    A permission mismatch is silence, not an error, which is the same shape as the compose
    variable that never reached the container in v0.10.1. So it is checked once, at the front,
    with a message that names the fix.

    It probes the *artifacts*, not just the mount point. v0.10.3 checked only that a new file
    could be created in `/data`, and so shipped a guard that could not see the failure it was
    written for: upgrading a box that had been running as root leaves `/data` itself writable
    with every existing child still owned by root. The new file lands at the top level, the probe
    passes, the app comes up healthy — `/api/health` returns 200 because it only reads — and the
    nightly retention prune is the first thing to actually write, dying on `attempt to write a
    readonly database`. That is the exact symptom this function exists to prevent, and it
    happened in production on 2026-08-29.
    """
    uid = getattr(os, "getuid", lambda: -1)()
    gid = getattr(os, "getgid", lambda: -1)()

    def unwritable(target: Path, exc: OSError) -> DataDirNotWritableError:
        where = str(target) if target == data_dir else f"{target} (inside {data_dir})"
        return DataDirNotWritableError(
            f"{where} is not writable by uid={uid} gid={gid} ({exc.strerror}). "
            f"The app runs as a non-root user and {data_dir} is a bind mount from the host, so "
            f"the host directory decides. Fix it on the host with "
            f"`sudo chown -R {uid}:{gid} ./data`, or set APP_UID/APP_GID in .env to whoever "
            f"owns ./data and restart."
        )

    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        _probe_new_entry(data_dir)
    except OSError as exc:
        raise unwritable(data_dir, exc) from exc

    # These names mirror Config.db_path / audio_dir / cache_dir. Only what already exists is
    # worth probing: anything missing is created later in the lifespan, by this process, and is
    # therefore ours by construction.
    db_path = data_dir / "episodes.db"
    if db_path.exists():
        try:
            # Append opens for writing without writing a byte — no content, size or mtime change.
            with db_path.open("ab"):
                pass
        except OSError as exc:
            raise unwritable(db_path, exc) from exc

    for subdir in (data_dir / "audio", data_dir / "cache"):
        if subdir.is_dir():
            try:
                _probe_new_entry(subdir)
            except OSError as exc:
                raise unwritable(subdir, exc) from exc


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_data_dir_writable(config.data_dir)
    db.init_db(config.db_path)
    config.audio_dir.mkdir(parents=True, exist_ok=True)
    config.cache_dir.mkdir(parents=True, exist_ok=True)
    await jobs.start()
    retention_task = asyncio.create_task(
        retention.run_periodically(
            config.db_path,
            config.audio_dir,
            config.cache_dir,
            lambda: _settings()["retention_days"],
        )
    )
    broadcast_task = asyncio.create_task(auto_broadcast_loop())
    log.info(
        "TLDR Radio up — port %d, kokoro %s, auto-broadcast %s",
        config.app_port, config.kokoro_url, config.auto_broadcast_time or "off",
    )
    yield
    for task in (retention_task, broadcast_task):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await jobs.stop()


app = FastAPI(title="TLDR Radio", version="0.4.0", lifespan=lifespan)


@app.middleware("http")
async def no_heuristic_cache(request, call_next):
    """Force the browser to revalidate assets (ETag still yields fast 304s).

    Local single-user app: never serve stale HTML/CSS/JS. Without an explicit
    Cache-Control, browsers heuristic-cache static files and miss updates.
    """
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache"
    return response


# --------------------------------------------------------------------------- models
class JobRequest(BaseModel):
    editions: list[str]
    date: str
    voice: str | None = None
    regenerate: bool = False


class PlaybackRequest(BaseModel):
    chapter_idx: int
    position_seconds: float


class AuditionRequest(BaseModel):
    voice: str


class SettingsRequest(BaseModel):
    default_voice: str | None = None
    retention_days: int | None = None
    playback_speed: str | None = None
    # Which newsletters are offered on the Desk, and which run overnight (auto ⊆ desk).
    desk_editions: list[str] | None = None
    auto_editions: list[str] | None = None
    # Sponsor reads. Applied at build time, so a change lands on the NEXT broadcast — episodes
    # already on disk keep whatever they were built with.
    include_sponsors: bool | None = None
    sponsor_voice: str | None = None


# --------------------------------------------------------------------------- helpers
def _csv(value: str | None) -> tuple[str, ...]:
    return tuple(part.strip() for part in (value or "").split(",") if part.strip())


def _settings() -> dict:
    with db.connect(config.db_path) as conn:
        # Env seeds the first read and nothing more: AUTO_BROADCAST_EDITIONS still configures a
        # fresh box from .env, but once the UI saves a list the table wins and no redeploy is
        # needed to change it. The Desk defaults to the three editions it always shipped with,
        # so an env var that narrows the overnight run doesn't also empty the Desk.
        desk = _csv(db.get_setting(conn, "desk_editions", ",".join(editions.DEFAULT_EDITIONS)))
        auto = _csv(
            db.get_setting(conn, "auto_editions", ",".join(config.auto_broadcast_editions))
        )
        settings = {
            "default_voice": db.get_setting(conn, "default_voice", config.default_voice),
            "retention_days": int(
                db.get_setting(conn, "retention_days", str(config.retention_days))
            ),
            "playback_speed": db.get_setting(conn, "playback_speed", "1.0"),
            "include_sponsors": db.get_setting(
                conn, "include_sponsors", "1" if config.include_sponsors else "0"
            )
            == "1",
            "sponsor_voice": db.get_setting(conn, "sponsor_voice", config.sponsor_voice),
        }
    auto = editions.known(auto)
    # Auto implies Desk — an edition can't broadcast nightly while being invisible on the Desk.
    # Enforced here, on every read, so the invariant holds no matter how the rows were written.
    settings["desk_editions"] = list(editions.known(set(desk) | set(auto)))
    settings["auto_editions"] = list(auto)
    return settings


def _delete_episode(episode_id: int) -> None:
    with db.connect(config.db_path) as conn:
        db.delete_episode(conn, episode_id)
    shutil.rmtree(config.audio_dir / str(episode_id), ignore_errors=True)


async def notify(title: str, message: str) -> None:
    """Best-effort webhook notification (ntfy-style: message body + Title header)."""
    if not config.ntfy_url:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(config.ntfy_url, content=message.encode(), headers={"Title": title})
    except httpx.HTTPError as exc:
        log.warning("notify failed: %s", exc)


async def _run_auto_broadcast() -> None:
    """Queue today's editions, wait for them, and keep re-checking any not published yet.

    tldr.tech posts at a different time every day — and not at all on weekends/holidays — so an
    edition that hasn't landed is re-checked on a bounded, jittered schedule instead of being
    written off after one shot. Failures are re-tried on the same schedule: most are transient
    (Kokoro still warming, a network blip), and a re-run is idempotent — a ready episode is
    adopted as-is and a partial one is rebuilt from scratch. Only the *final* state of each
    edition is reported, so a run that recovers never sends a failure notification.
    """
    global last_auto_broadcast
    today = datetime.now().strftime("%Y-%m-%d")
    settings = _settings()
    voice = settings["default_voice"]
    tonight = settings["auto_editions"]
    deadline = time.monotonic() + config.auto_broadcast_retry_hours * 3600

    results: dict[str, dict] = {}
    active: dict[str, Job] = {}
    for edition in tonight:
        with db.connect(config.db_path) as conn:
            existing = db.find_episode(conn, edition, today)
        if existing is not None and existing["status"] == "ready":
            results[edition] = {"status": "already-ready", "error": None, "note": None}
        else:
            active[edition] = await jobs.enqueue(
                edition,
                today,
                voice,
                include_sponsors=settings["include_sponsors"],
                sponsor_voice=settings["sponsor_voice"],
            )

    attempts = 0
    while active:
        attempts += 1
        while any(job.status not in TERMINAL for job in active.values()):
            await asyncio.sleep(20)
        for edition, job in list(active.items()):
            if job.status is not JobStatus.READY and time.monotonic() < deadline:
                continue  # not published yet, or a failure worth another go — keep it active
            results[edition] = {"status": job.status.value, "error": job.error, "note": job.note}
            del active[edition]
        if not active:
            break
        wait = RETRY_INTERVAL_SECONDS + random.uniform(-RETRY_JITTER_SECONDS, RETRY_JITTER_SECONDS)
        log.info(
            "auto-broadcast %s: re-checking %s in %.0f min",
            today,
            ", ".join(f"{e} ({j.status.value})" for e, j in sorted(active.items())),
            wait / 60,
        )
        await asyncio.sleep(wait)
        for job in active.values():
            await jobs.retry(job.id)

    last_auto_broadcast = {
        "date": today,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "attempts": attempts,
        "results": [{"edition": e, **results[e]} for e in tonight if e in results],
    }
    failed = [e for e, r in results.items() if r["status"] == "failed"]
    skipped = [e for e, r in results.items() if r["status"] == "skipped"]
    ready = [e for e, r in results.items() if r["status"] in ("ready", "already-ready")]
    log.info(
        "auto-broadcast %s done in %d attempt(s): %d ready, %d not published, %d failed",
        today, attempts, len(ready), len(skipped), len(failed),
    )
    if failed:
        detail = "; ".join(f"{e}: {results[e]['error']}" for e in failed)
        await notify("TLDR Radio — broadcast issue", f"{today} — {len(failed)} failed: {detail}")
    elif ready:
        extra = f" ({len(skipped)} not published)" if skipped else ""
        await notify(
            "TLDR Radio ready",
            f"{today} — {len(ready)} ready{extra}: {', '.join(ready)}.",
        )
    # Nothing published at all (weekend/holiday) → the log line above is the whole story: no push.


async def auto_broadcast_loop() -> None:
    """Sleep until auto_broadcast_time (local) each day, then run the broadcast."""
    while True:
        target = config.auto_broadcast_time
        if not target:
            await asyncio.sleep(3600)
            continue
        try:
            hour, minute = (int(x) for x in target.split(":"))
        except ValueError:
            log.warning("auto-broadcast: invalid AUTO_BROADCAST_TIME %r", target)
            await asyncio.sleep(3600)
            continue
        now = datetime.now()
        nxt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        await asyncio.sleep((nxt - now).total_seconds())
        try:
            await _run_auto_broadcast()
        except Exception:
            log.exception("auto-broadcast run failed")


# --------------------------------------------------------------------------- health / voices
@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "kokoro": "up" if await kokoro.health() else "down",
        "db": config.db_path.exists(),
        "default_voice": _settings()["default_voice"],
    }


@app.get("/api/voices")
async def voices() -> dict:
    return {"voices": await kokoro.list_voices()}


@app.get("/api/plex/status")
async def plex_status() -> dict:
    """What's streaming on Plex right now (for the broadcast CPU-contention guard)."""
    result = await plex.now_playing(config.plex_url, config.plex_token)
    if result is None:
        return {"configured": config.plex_enabled, "playing": False, "count": 0, "sessions": []}
    return {"configured": True, **result}


@app.get("/api/auto-broadcast/status")
async def auto_broadcast_status() -> dict:
    """Outcome of the most recent overnight auto-broadcast (null until one runs)."""
    return {
        "configured_time": config.auto_broadcast_time or None,
        "editions": _settings()["auto_editions"],
        "last": last_auto_broadcast,
    }


@app.get("/api/editions")
async def list_editions() -> dict:
    """The newsletter catalog with each edition's Desk / Auto state and a nightly estimate.

    One payload drives both the Desk's station buttons and the Settings stations panel, so the
    two can't drift apart.
    """
    settings = _settings()
    desk, auto = set(settings["desk_editions"]), set(settings["auto_editions"])
    with db.connect(config.db_path) as conn:
        means = db.mean_duration_by_edition(conn)
    known_means = [v for v in means.values() if v]
    # An edition that has never run borrows the average of the ones that have; with nothing to
    # go on at all the estimate is simply unavailable rather than invented.
    fallback = sum(known_means) / len(known_means) if known_means else None
    catalog = [
        {
            "slug": e.slug,
            "name": e.name,
            "tagline": e.tagline,
            "description": e.description,
            "desk": e.slug in desk,
            "auto": e.slug in auto,
            "mean_duration_seconds": means.get(e.slug),
        }
        for e in editions.EDITIONS.values()
    ]
    estimate = None
    if fallback is not None:
        estimate = sum(means.get(slug, fallback) for slug in settings["auto_editions"])
    return {"editions": catalog, "auto_audio_seconds": estimate}


@app.get("/api/parser/health")
async def parser_health(date: str | None = None) -> dict:
    """Fetch + parse the Desk's editions for a date and report story counts — no synthesis.

    A quick way to catch a tldr.tech layout change before it silently breaks a broadcast.

    Fetches are spaced out: tldr.tech throttles bursts, and a throttled response looks exactly
    like "not published" (a 404, or a redirect to the landing page). Broadcasts never hit this
    because the worker runs one job at a time with minutes of synthesis in between — but this
    route is a dozen-plus fetches back to back, which is precisely the pattern that trips it.
    """
    day = date or datetime.now().strftime("%Y-%m-%d")
    results: dict = {}
    for i, edition in enumerate(_settings()["desk_editions"]):
        if i:
            await asyncio.sleep(PARSER_HEALTH_DELAY_SECONDS)
        try:
            html = await fetch_archive(edition, day, config.cache_dir)
            results[edition] = {"ok": True, "stories": len(real_stories(parse_edition(html)))}
        except Exception as exc:  # NotPublished / EmptyParse / network → report, don't 500
            results[edition] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"date": day, "editions": results}


# --------------------------------------------------------------------------- jobs
@app.post("/api/jobs")
async def create_jobs(req: JobRequest) -> dict:
    """Queue one job per edition; skip ready duplicates unless regenerate.

    Slugs are checked against the catalog first — an unknown one would otherwise queue a job
    that fetches a URL tldr.tech will never serve and report it as "not published".
    """
    settings = _settings()
    voice = req.voice or settings["default_voice"]
    wanted = editions.known(req.editions)
    rejected = sorted(set(req.editions) - set(wanted))
    if rejected:
        log.warning("ignoring unknown edition(s): %s", ", ".join(rejected))
    created: list[dict] = []
    skipped: list[str] = []
    for edition in wanted:
        with db.connect(config.db_path) as conn:
            existing = db.find_episode(conn, edition, req.date)
        if existing is not None and existing["status"] == "ready" and not req.regenerate:
            skipped.append(edition)
            continue
        if existing is not None and req.regenerate:
            _delete_episode(existing["id"])
        job = await jobs.enqueue(
            edition,
            req.date,
            voice,
            include_sponsors=settings["include_sponsors"],
            sponsor_voice=settings["sponsor_voice"],
        )
        created.append(job.as_event())
    return {"created": created, "skipped": skipped, "rejected": rejected}


@app.get("/api/jobs")
async def list_jobs() -> list[dict]:
    return jobs.snapshot()


@app.get("/api/jobs/stream")
async def stream_jobs() -> StreamingResponse:
    """Server-Sent Events: an initial snapshot, then one event per job transition."""

    async def gen():
        queue = jobs.subscribe()
        try:
            for event in jobs.snapshot():
                yield f"data: {json.dumps(event)}\n\n"
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            jobs.unsubscribe(queue)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/jobs/{job_id}/retry")
async def retry_job(job_id: int) -> dict:
    job = await jobs.retry(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No failed job with that id")
    return job.as_event()


# --------------------------------------------------------------------------- episodes
@app.get("/api/episodes")
async def episodes() -> list[dict]:
    with db.connect(config.db_path) as conn:
        return [dict(row) for row in db.list_episodes(conn)]


@app.get("/api/episodes/{episode_id}")
async def episode_detail(episode_id: int) -> dict:
    with db.connect(config.db_path) as conn:
        episode = db.get_episode(conn, episode_id)
        if episode is None:
            raise HTTPException(status_code=404, detail="No such episode")
        chapters = db.get_chapters(conn, episode_id)
        playback = db.get_playback(conn, episode_id)
    return {
        "episode": dict(episode),
        "chapters": [dict(c) for c in chapters],
        "playback": dict(playback) if playback else None,
    }


@app.delete("/api/episodes/{episode_id}")
async def delete_episode(episode_id: int) -> dict:
    _delete_episode(episode_id)
    return {"deleted": episode_id}


# --------------------------------------------------------------------------- download
def _download_name(edition: str, issue_date: str) -> str:
    """`tldr-tech-2026-08-10.mp3` — sorts chronologically inside an edition, safe everywhere."""
    slug = re.sub(r"[^a-z0-9]+", "-", f"tldr-{edition}".lower()).strip("-")
    return f"{slug}-{issue_date}.mp3"


@app.get("/api/episodes/{episode_id}/download")
async def download_episode(episode_id: int) -> FileResponse:
    """Serve the whole episode as one tagged mp3, building and caching it on first request.

    Built on demand rather than at broadcast time. The backlog assumed this trade was
    "disk vs CPU on a box that has little to spare", but joining is a byte copy, not a
    re-encode — a few MB, milliseconds — so only episodes actually downloaded cost any disk,
    and the file lands beside its chapters where retention and delete already remove it.
    """
    with db.connect(config.db_path) as conn:
        episode = db.get_episode(conn, episode_id)
        if episode is None:
            raise HTTPException(status_code=404, detail="No such episode")
        if episode["status"] != "ready":
            raise HTTPException(status_code=409, detail="Episode is not ready yet")
        chapters = db.get_chapters(conn, episode_id)

    ep_dir = config.audio_dir / str(episode_id)
    combined = ep_dir / "episode.mp3"
    filename = _download_name(episode["edition"], episode["issue_date"])

    if not combined.exists():
        paths = [ep_dir / f"{c['idx']}.mp3" for c in chapters]
        missing = [p.name for p in paths if not p.exists()]
        if not paths or missing:
            raise HTTPException(
                status_code=409,
                detail=f"Episode audio is incomplete (missing {', '.join(missing) or 'all'})",
            )
        try:
            await asyncio.to_thread(concat_mp3s, paths, combined)
            await asyncio.to_thread(
                write_tags,
                combined,
                edition_name=editions.name_for(episode["edition"]),
                issue_date=episode["issue_date"],
                story_count=int(episode["story_count"]),
            )
        except NotConcatenableError as exc:
            # The audio stopped being safe to byte-append — a Kokoro change, most likely.
            # Say so rather than serving a file that plays wrong.
            log.error("episode %d cannot be joined: %s", episode_id, exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return FileResponse(
        combined,
        media_type="audio/mpeg",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --------------------------------------------------------------------------- audio (Range-capable)
@app.get("/api/audio/{episode_id}/{idx}")
async def audio(episode_id: int, idx: int) -> FileResponse:
    path = config.audio_dir / str(episode_id) / f"{idx}.mp3"
    if not path.exists():
        raise HTTPException(status_code=404, detail="No audio for that chapter")
    return FileResponse(path, media_type="audio/mpeg")


# --------------------------------------------------------------------------- playback
@app.put("/api/playback/{episode_id}")
async def put_playback(episode_id: int, req: PlaybackRequest) -> dict:
    with db.connect(config.db_path) as conn:
        db.set_playback(conn, episode_id, req.chapter_idx, req.position_seconds)
    return {"ok": True}


# --------------------------------------------------------------------------- settings
@app.get("/api/settings")
async def get_settings() -> dict:
    return _settings()


@app.put("/api/settings")
async def put_settings(req: SettingsRequest) -> dict:
    with db.connect(config.db_path) as conn:
        if req.default_voice is not None:
            db.set_setting(conn, "default_voice", req.default_voice)
        if req.retention_days is not None:
            db.set_setting(conn, "retention_days", str(req.retention_days))
        if req.playback_speed is not None:
            db.set_setting(conn, "playback_speed", req.playback_speed)
        if req.include_sponsors is not None:
            db.set_setting(conn, "include_sponsors", "1" if req.include_sponsors else "0")
        if req.sponsor_voice is not None:
            db.set_setting(conn, "sponsor_voice", req.sponsor_voice)
        # Unknown slugs are dropped rather than rejected: a retired edition left in an old
        # payload shouldn't 400 the whole save.
        if req.desk_editions is not None:
            db.set_setting(conn, "desk_editions", ",".join(editions.known(req.desk_editions)))
        if req.auto_editions is not None:
            db.set_setting(conn, "auto_editions", ",".join(editions.known(req.auto_editions)))
    return _settings()


# --------------------------------------------------------------------------- voice audition
async def _audition_file(voice: str) -> FileResponse:
    """Serve a voice's audition clip, synthesizing it on first request (seconds on CPU)."""
    if not _VOICE_RE.match(voice):
        raise HTTPException(status_code=400, detail="Invalid voice id")
    out_path = config.audio_dir / "auditions" / f"{voice}.mp3"
    if not out_path.exists():
        await kokoro.synthesize(AUDITION_LINE, voice, out_path)
    return FileResponse(out_path, media_type="audio/mpeg")


@app.get("/api/voices/audition/{voice}")
async def audition_get(voice: str) -> FileResponse:
    """GET form so an <audio> element can own the slow first synth itself.

    Playback must start inside the click that triggered it or the browser's autoplay policy
    rejects it — so the UI points `audio.src` here rather than awaiting a fetch. See app.js.
    """
    return await _audition_file(voice)


@app.post("/api/voices/audition")
async def audition(req: AuditionRequest) -> FileResponse:
    """POST form kept for API compatibility (spec §"API surface")."""
    return await _audition_file(req.voice)


# --------------------------------------------------------------------------- dev harness
@app.post("/api/dev/pipeline")
async def dev_pipeline(
    edition: str,
    date: str,
    voice: str | None = None,
    limit: int | None = None,
) -> dict:
    """DEV: run fetch → parse → script → synth for one edition/date (no persistence)."""
    voice = voice or _settings()["default_voice"]
    started = time.time()
    html = await fetch_archive(edition, date, config.cache_dir)
    stories = real_stories(parse_edition(html))
    chapters = build_scripts(edition, date, stories)
    if limit is not None:
        chapters = chapters[:limit]
    out_dir = config.audio_dir / f"dev-{edition}-{date}"
    paths = await synthesize_chapters(
        kokoro, chapters, voice, out_dir, config.max_concurrent_synth
    )
    return {
        "edition": edition,
        "date": date,
        "voice": voice,
        "stories": len(stories),
        "chapters_synthesized": len(paths),
        "seconds": round(time.time() - started, 1),
    }


# --------------------------------------------------------------------------- static UI
# Mounted LAST so /api/* routes win. html=True serves index.html at "/" and *.html by path.
app.mount("/", StaticFiles(directory=str(APP_ROOT / "static"), html=True), name="static")
