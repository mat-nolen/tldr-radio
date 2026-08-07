"""14-day retention prune (spec §"Retention").

Deletes episodes (cascading chapters + playback), their mp3 directories, and cached HTML
older than `retention_days`. A file that can't be removed is skipped and retried next run.
Runs nightly via `run_periodically`, started from the app lifespan.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import db

log = logging.getLogger(__name__)

DAY_SECONDS = 86_400


def prune(db_path: Path, audio_dir: Path, cache_dir: Path, retention_days: int) -> int:
    """Remove episodes + audio + cached HTML older than `retention_days`; return count pruned."""
    now = datetime.now(UTC)
    cutoff_iso = (now - timedelta(days=retention_days)).isoformat()
    cutoff_ts = (now - timedelta(days=retention_days)).timestamp()

    removed = 0
    with db.connect(db_path) as conn:
        old = conn.execute(
            "SELECT id FROM episodes WHERE created_at < ?", (cutoff_iso,)
        ).fetchall()
        for row in old:
            episode_id = row["id"]
            conn.execute("DELETE FROM episodes WHERE id = ?", (episode_id,))
            shutil.rmtree(audio_dir / str(episode_id), ignore_errors=True)
            removed += 1
        conn.commit()

    if cache_dir.exists():
        for html in cache_dir.glob("*.html"):
            try:
                if html.stat().st_mtime < cutoff_ts:
                    html.unlink()
            except OSError:
                log.warning("retention: could not remove cache file %s", html)

    if removed:
        log.info("retention: pruned %d episode(s) older than %d days", removed, retention_days)
    return removed


async def run_periodically(
    db_path: Path,
    audio_dir: Path,
    cache_dir: Path,
    retention_days: Callable[[], int],
    interval_seconds: int = DAY_SECONDS,
) -> None:
    """Run `prune` on startup and then every `interval_seconds`, reading the current setting."""
    while True:
        try:
            prune(db_path, audio_dir, cache_dir, retention_days())
        except Exception:
            log.exception("retention prune failed")
        await asyncio.sleep(interval_seconds)
