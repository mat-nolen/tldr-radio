"""SQLite persistence — stdlib sqlite3, no ORM. Schema per spec §17.

Parameterized SQL throughout. Callers use `connect()` as a context manager.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
  id               INTEGER PRIMARY KEY,
  edition          TEXT    NOT NULL,          -- a slug from app/editions.py (14 newsletters)
  issue_date       TEXT    NOT NULL,          -- 'YYYY-MM-DD'
  title            TEXT    NOT NULL,
  source_url       TEXT    NOT NULL,
  voice            TEXT    NOT NULL,
  status           TEXT    NOT NULL,          -- JobStatus: queued...synthesizing|ready|failed
  error            TEXT,
  story_count      INTEGER NOT NULL DEFAULT 0,
  duration_seconds REAL,
  created_at       TEXT    NOT NULL,
  ready_at         TEXT,
  UNIQUE (edition, issue_date)
);

CREATE TABLE IF NOT EXISTS chapters (
  id               INTEGER PRIMARY KEY,
  episode_id       INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  idx              INTEGER NOT NULL,          -- 0-based playback order
  kind             TEXT    NOT NULL,          -- 'intro' | 'story' | 'outro'
  section          TEXT,
  headline         TEXT,
  summary_source   TEXT,
  script_text      TEXT    NOT NULL,
  url              TEXT,
  read_time        TEXT,
  audio_path       TEXT,
  duration_seconds REAL,
  UNIQUE (episode_id, idx)
);

CREATE TABLE IF NOT EXISTS playback (
  episode_id       INTEGER PRIMARY KEY REFERENCES episodes(id) ON DELETE CASCADE,
  chapter_idx      INTEGER NOT NULL,
  position_seconds REAL    NOT NULL,
  updated_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS page_cache (
  url        TEXT PRIMARY KEY,
  fetched_at TEXT NOT NULL,
  path       TEXT NOT NULL
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection with Row access and foreign keys enforced."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path) -> None:
    """Create the data directory and apply the schema (idempotent)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")  # concurrent reads while the worker writes
        conn.executescript(SCHEMA)
    log.info("SQLite ready at %s", db_path)


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    """Return a single-row setting value, or `default` if unset."""
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Upsert a single setting."""
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def mean_duration_by_edition(conn: sqlite3.Connection) -> dict[str, float]:
    """Average ready-episode length in seconds, per edition — powers the nightly estimate.

    Only editions that have actually produced an episode appear; the caller decides what to
    assume for one that has never run.
    """
    rows = conn.execute(
        "SELECT edition, AVG(duration_seconds) AS mean FROM episodes "
        "WHERE status = 'ready' AND duration_seconds IS NOT NULL GROUP BY edition"
    ).fetchall()
    return {row["edition"]: row["mean"] for row in rows}


def now_iso() -> str:
    """UTC timestamp, ISO-8601."""
    return datetime.now(UTC).isoformat()


# ---- episodes -------------------------------------------------------------

def create_episode(
    conn: sqlite3.Connection,
    edition: str,
    issue_date: str,
    title: str,
    source_url: str,
    voice: str,
) -> int:
    cur = conn.execute(
        "INSERT INTO episodes (edition, issue_date, title, source_url, voice, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'queued', ?)",
        (edition, issue_date, title, source_url, voice, now_iso()),
    )
    conn.commit()
    return int(cur.lastrowid)


def set_episode_status(
    conn: sqlite3.Connection, episode_id: int, status: str, error: str | None = None
) -> None:
    conn.execute(
        "UPDATE episodes SET status = ?, error = ? WHERE id = ?", (status, error, episode_id)
    )
    conn.commit()


def set_episode_ready(
    conn: sqlite3.Connection, episode_id: int, story_count: int, duration_seconds: float
) -> None:
    conn.execute(
        "UPDATE episodes SET status='ready', story_count=?, duration_seconds=?, ready_at=?, "
        "error=NULL WHERE id=?",
        (story_count, duration_seconds, now_iso(), episode_id),
    )
    conn.commit()


def find_episode(conn: sqlite3.Connection, edition: str, issue_date: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM episodes WHERE edition=? AND issue_date=?", (edition, issue_date)
    ).fetchone()


def get_episode(conn: sqlite3.Connection, episode_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()


def list_episodes(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM episodes ORDER BY issue_date DESC, edition").fetchall()


def delete_episode(conn: sqlite3.Connection, episode_id: int) -> None:
    conn.execute("DELETE FROM episodes WHERE id=?", (episode_id,))
    conn.commit()


# ---- chapters -------------------------------------------------------------

def insert_chapter(conn: sqlite3.Connection, episode_id: int, ch) -> None:
    """Insert a chapter row from a ScriptedChapter-like object (duck-typed)."""
    conn.execute(
        "INSERT INTO chapters (episode_id, idx, kind, section, headline, summary_source, "
        "script_text, url, read_time) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            episode_id, ch.idx, ch.kind, ch.section, ch.headline,
            ch.summary_source, ch.script_text, ch.url, ch.read_time,
        ),
    )
    conn.commit()


def set_chapter_audio(
    conn: sqlite3.Connection, episode_id: int, idx: int, audio_path: str, duration_seconds: float
) -> None:
    conn.execute(
        "UPDATE chapters SET audio_path=?, duration_seconds=? WHERE episode_id=? AND idx=?",
        (audio_path, duration_seconds, episode_id, idx),
    )
    conn.commit()


def get_chapters(conn: sqlite3.Connection, episode_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM chapters WHERE episode_id=? ORDER BY idx", (episode_id,)
    ).fetchall()


# ---- playback -------------------------------------------------------------

def get_playback(conn: sqlite3.Connection, episode_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM playback WHERE episode_id=?", (episode_id,)).fetchone()


def set_playback(
    conn: sqlite3.Connection, episode_id: int, chapter_idx: int, position_seconds: float
) -> None:
    conn.execute(
        "INSERT INTO playback (episode_id, chapter_idx, position_seconds, updated_at) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(episode_id) DO UPDATE SET "
        "chapter_idx=excluded.chapter_idx, position_seconds=excluded.position_seconds, "
        "updated_at=excluded.updated_at",
        (episode_id, chapter_idx, position_seconds, now_iso()),
    )
    conn.commit()
