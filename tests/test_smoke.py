"""Skeleton smoke tests — schema initializes, config loads, pronunciation applies."""

from __future__ import annotations

from pathlib import Path

from app import db
from app.config import load_config
from app.pipeline.pronounce import apply_pronunciations


def test_config_defaults(monkeypatch):
    for var in ("APP_PORT", "DEFAULT_VOICE", "KOKORO_URL"):
        monkeypatch.delenv(var, raising=False)
    cfg = load_config()
    assert cfg.app_port == 7777
    assert cfg.default_voice == "af_heart"
    assert cfg.kokoro_url == "http://localhost:8880"


def test_db_init_and_settings(tmp_path: Path):
    db_path = tmp_path / "t.db"
    db.init_db(db_path)
    with db.connect(db_path) as conn:
        assert db.get_setting(conn, "missing", "fallback") == "fallback"
        db.set_setting(conn, "default_voice", "af_bella")
        assert db.get_setting(conn, "default_voice") == "af_bella"
        tables = {
            row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {"episodes", "chapters", "playback", "settings", "page_cache"} <= tables


def test_pronounce_applies():
    out = apply_pronunciations("The API and GPT-4o shipped on iOS, patch CVE-2026-1234.")
    assert "A P I" in out
    assert "G P T four oh" in out
    assert "i O S" in out
    assert "C V E 2026 1234" in out
