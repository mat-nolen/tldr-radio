"""Retention prune — old episodes + audio + cache removed, recent survive (spec acceptance)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app import db
from app.retention import prune


def _seed_episode(conn, edition: str, days_old: int) -> int:
    created = (datetime.now(UTC) - timedelta(days=days_old)).isoformat()
    cur = conn.execute(
        "INSERT INTO episodes (edition, issue_date, title, source_url, voice, status, created_at) "
        "VALUES (?, '2026-07-22', 'T', 'u', 'af_heart', 'ready', ?)",
        (edition, created),
    )
    conn.commit()
    return int(cur.lastrowid)


def test_prune_removes_old_keeps_recent(tmp_path: Path):
    db_path = tmp_path / "t.db"
    audio = tmp_path / "audio"
    cache = tmp_path / "cache"
    audio.mkdir()
    cache.mkdir()
    db.init_db(db_path)

    with db.connect(db_path) as conn:
        old_id = _seed_episode(conn, "ai", days_old=30)
        recent_id = _seed_episode(conn, "tech", days_old=1)

    for ep_id in (old_id, recent_id):
        (audio / str(ep_id)).mkdir()
        (audio / str(ep_id) / "0.mp3").write_bytes(b"x")

    old_html = cache / "infosec-old.html"
    old_html.write_text("x")
    old_ts = (datetime.now(UTC) - timedelta(days=30)).timestamp()
    os.utime(old_html, (old_ts, old_ts))
    fresh_html = cache / "ai-fresh.html"
    fresh_html.write_text("x")

    removed = prune(db_path, audio, cache, retention_days=14)

    assert removed == 1
    with db.connect(db_path) as conn:
        ids = {r["id"] for r in conn.execute("SELECT id FROM episodes")}
    assert recent_id in ids and old_id not in ids
    assert not (audio / str(old_id)).exists()
    assert (audio / str(recent_id)).exists()
    assert not old_html.exists()
    assert fresh_html.exists()
