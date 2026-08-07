"""Re-running a job must not collide with what its first attempt already persisted.

`episodes` has UNIQUE (edition, issue_date), and a job that dies during synthesis has already
written its episode row — so the retry path has to reuse or replace it. This matters more now
that the overnight broadcast retries failures automatically, not just when someone clicks Retry.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from app import db, worker
from app.worker import TERMINAL, JobQueue, JobStatus

FIXTURE = Path(__file__).parent / "fixtures" / "tech-2026-07-22.html"


class FlakyKokoro:
    """Fails the first `fail_first` synthesis calls, then writes placeholder mp3s."""

    def __init__(self, fail_first: int = 0) -> None:
        self.fail_first = fail_first
        self.calls = 0

    async def synthesize(self, text: str, voice: str, out_path: Path) -> Path:
        self.calls += 1
        if self.fail_first > 0:
            self.fail_first -= 1
            raise RuntimeError("kokoro fell over")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"\xff\xfb\x90\x00placeholder")  # mp3_duration tolerates this
        return out_path


def setup_worker(tmp_path: Path, monkeypatch, fail_first: int = 0) -> tuple[JobQueue, object]:
    cfg = replace(worker.config, data_dir=tmp_path, max_concurrent_synth=2)
    monkeypatch.setattr(worker, "config", cfg)
    db.init_db(cfg.db_path)

    async def fake_fetch(edition: str, date: str, cache_dir: Path) -> str:
        return FIXTURE.read_text(encoding="utf-8")

    monkeypatch.setattr(worker, "fetch_archive", fake_fetch)
    return JobQueue(FlakyKokoro(fail_first)), cfg


async def until_terminal(job, timeout: float = 10.0) -> None:
    waited = 0.0
    while job.status not in TERMINAL and waited < timeout:
        await asyncio.sleep(0.02)
        waited += 0.02


def episode_rows(cfg) -> list[tuple[int, str]]:
    with db.connect(cfg.db_path) as conn:
        return [(r["id"], r["status"]) for r in conn.execute("SELECT id, status FROM episodes")]


def test_retry_after_synthesis_failure_lands_one_ready_episode(tmp_path: Path, monkeypatch):
    queue, cfg = setup_worker(tmp_path, monkeypatch, fail_first=1)

    async def scenario():
        await queue.start()
        job = await queue.enqueue("tech", "2026-07-22", "af_heart")
        await until_terminal(job)
        assert job.status is JobStatus.FAILED, job.status
        first_episode_id = job.episode_id
        assert first_episode_id is not None  # the failed attempt did persist a row

        await queue.retry(job.id)
        await until_terminal(job)
        await queue.stop()
        return job, first_episode_id

    job, _ = asyncio.run(scenario())
    assert job.status is JobStatus.READY, f"retry failed: {job.error}"
    rows = episode_rows(cfg)
    assert len(rows) == 1, f"expected one episode row, got {rows}"
    assert rows[0][1] == "ready"


def test_rerun_of_an_already_ready_episode_is_a_no_op(tmp_path: Path, monkeypatch):
    """A stale card's Retry must never clobber an episode that has since been produced."""
    queue, cfg = setup_worker(tmp_path, monkeypatch)

    async def scenario():
        await queue.start()
        first = await queue.enqueue("tech", "2026-07-22", "af_heart")
        await until_terminal(first)
        assert first.status is JobStatus.READY, first.error

        second = await queue.enqueue("tech", "2026-07-22", "af_heart")
        await until_terminal(second)
        await queue.stop()
        return first, second

    first, second = asyncio.run(scenario())
    assert second.status is JobStatus.READY
    assert second.episode_id == first.episode_id  # reused, not duplicated
    assert len(episode_rows(cfg)) == 1
