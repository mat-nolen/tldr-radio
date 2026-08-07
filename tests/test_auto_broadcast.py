"""Overnight auto-broadcast: re-check what isn't published, report what actually failed.

tldr.tech posts at a different time each day, so "not published yet" has to be retried rather
than written off — while a genuine failure must be reported at once and never spammed.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import replace

import pytest

from app import main
from app.worker import Job, JobStatus


class FakeJobs:
    """Stand-in for the JobQueue: each enqueue/retry pops the next scripted status."""

    def __init__(self, script: dict[str, list[JobStatus]]) -> None:
        self.script = script
        self.jobs: dict[int, Job] = {}
        self.retried: list[str] = []
        self._next_id = 0

    def _advance(self, job: Job) -> None:
        job.status = self.script[job.edition].pop(0)
        job.note = "Not published yet" if job.status is JobStatus.SKIPPED else None
        job.error = "Kokoro unreachable" if job.status is JobStatus.FAILED else None

    async def enqueue(self, edition: str, issue_date: str, voice: str) -> Job:
        self._next_id += 1
        job = Job(id=self._next_id, edition=edition, issue_date=issue_date, voice=voice)
        self._advance(job)
        self.jobs[job.id] = job
        return job

    async def retry(self, job_id: int) -> Job:
        job = self.jobs[job_id]
        self.retried.append(job.edition)
        self._advance(job)
        return job


@pytest.fixture
def broadcast(monkeypatch):
    """Run _run_auto_broadcast against a scripted queue, with no DB, sleeps or network."""
    pushes: list[tuple[str, str]] = []

    async def fake_notify(title: str, message: str) -> None:
        pushes.append((title, message))

    monkeypatch.setattr(main.db, "find_episode", lambda conn, edition, date: None)
    monkeypatch.setattr(main.db, "connect", lambda path: contextlib.nullcontext())
    monkeypatch.setattr(main, "notify", fake_notify)
    monkeypatch.setattr(main, "RETRY_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(main, "RETRY_JITTER_SECONDS", 0)

    def run(script: dict[str, list[JobStatus]], retry_hours: float = 4.0):
        # Which editions run overnight now comes from the settings table, not the env var.
        monkeypatch.setattr(
            main,
            "_settings",
            lambda: {"default_voice": "af_heart", "auto_editions": list(script)},
        )
        monkeypatch.setattr(
            main,
            "config",
            replace(main.config, auto_broadcast_retry_hours=retry_hours),
        )
        fake = FakeJobs(script)
        monkeypatch.setattr(main, "jobs", fake)
        asyncio.run(main._run_auto_broadcast())
        return main.last_auto_broadcast, fake, pushes

    return run


def statuses(result: dict) -> dict[str, str]:
    return {r["edition"]: r["status"] for r in result["results"]}


def test_not_published_is_rechecked_until_it_lands(broadcast):
    result, fake, pushes = broadcast(
        {"tech": [JobStatus.SKIPPED, JobStatus.SKIPPED, JobStatus.READY]}
    )
    assert statuses(result) == {"tech": "ready"}
    assert fake.retried == ["tech", "tech"]  # two re-checks, same job — not three new cards
    assert result["attempts"] == 3
    assert pushes and pushes[0][0] == "TLDR Radio ready"


def test_editions_are_rechecked_independently(broadcast):
    result, fake, _ = broadcast({
        "tech": [JobStatus.READY],
        "ai": [JobStatus.SKIPPED, JobStatus.READY],
        "infosec": [JobStatus.SKIPPED, JobStatus.SKIPPED, JobStatus.READY],
    })
    assert statuses(result) == {"tech": "ready", "ai": "ready", "infosec": "ready"}
    assert fake.retried == ["ai", "infosec", "infosec"]  # tech was never re-run


def test_weekend_gives_up_quietly_with_no_push(broadcast):
    """Nothing published all window: recorded as skipped, and no notification at all."""
    result, _, pushes = broadcast({"tech": [JobStatus.SKIPPED], "ai": [JobStatus.SKIPPED]},
                                  retry_hours=0)
    assert statuses(result) == {"tech": "skipped", "ai": "skipped"}
    assert pushes == []


def test_transient_failure_is_retried_and_never_reported(broadcast):
    """Most failures are transient (Kokoro warming, network blip) — recover quietly."""
    result, fake, pushes = broadcast({"tech": [JobStatus.FAILED, JobStatus.READY]})
    assert statuses(result) == {"tech": "ready"}
    assert fake.retried == ["tech"]
    assert pushes and pushes[0][0] == "TLDR Radio ready"  # no failure push for a run that recovered


def test_persistent_failure_is_reported_once_at_the_end(broadcast):
    result, fake, pushes = broadcast(
        {"tech": [JobStatus.FAILED, JobStatus.FAILED, JobStatus.FAILED]}, retry_hours=0
    )
    assert statuses(result) == {"tech": "failed"}
    assert fake.retried == []  # retries disabled → reported on the first pass
    assert len(pushes) == 1 and "broadcast issue" in pushes[0][0]
    assert "Kokoro unreachable" in pushes[0][1]


def test_mixed_skip_and_failure_are_both_retried(broadcast):
    result, fake, _ = broadcast({
        "tech": [JobStatus.FAILED, JobStatus.READY],
        "ai": [JobStatus.SKIPPED, JobStatus.SKIPPED, JobStatus.READY],
    })
    assert statuses(result) == {"tech": "ready", "ai": "ready"}
    assert fake.retried == ["tech", "ai", "ai"]


def test_partial_success_pushes_ready_and_counts_the_rest(broadcast):
    result, _, pushes = broadcast(
        {"tech": [JobStatus.READY], "ai": [JobStatus.SKIPPED]}, retry_hours=0
    )
    assert statuses(result) == {"tech": "ready", "ai": "skipped"}
    assert pushes[0][0] == "TLDR Radio ready"
    assert "1 not published" in pushes[0][1]


def test_retry_disabled_records_skipped_on_the_first_pass(broadcast):
    _, fake, _ = broadcast({"tech": [JobStatus.SKIPPED, JobStatus.READY]}, retry_hours=0)
    assert fake.retried == []
