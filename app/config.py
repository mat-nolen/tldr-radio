"""Application configuration, loaded once from environment variables (all optional)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = APP_ROOT.parent


@dataclass(frozen=True)
class Config:
    """Runtime configuration. Defaults suit local dev; Docker overrides via env."""

    app_port: int
    kokoro_url: str
    retention_days: int
    default_voice: str
    # Sponsor reads: kept by default so a fresh install plays the newsletter as published.
    # Both are first-read seeds only — the settings table wins once the UI has saved.
    include_sponsors: bool
    sponsor_voice: str
    max_concurrent_synth: int
    data_dir: Path
    # Optional Plex guard — warn before a manual broadcast if Plex is streaming.
    plex_url: str
    plex_token: str
    # Optional overnight auto-broadcast — "HH:MM" local time (empty disables).
    auto_broadcast_time: str
    auto_broadcast_editions: tuple[str, ...]
    # How long to keep re-checking editions that aren't published yet (0 disables retries).
    auto_broadcast_retry_hours: float
    # Optional ntfy (or any) webhook URL for broadcast success/failure notifications.
    ntfy_url: str

    @property
    def db_path(self) -> Path:
        return self.data_dir / "episodes.db"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def plex_enabled(self) -> bool:
        return bool(self.plex_url and self.plex_token)


def _env_bool(name: str, default: bool) -> bool:
    """Parse a boolean env var, tolerating the usual spellings."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_config() -> Config:
    """Build the Config from the environment, applying spec §20 defaults."""
    data_dir = Path(os.environ.get("DATA_DIR", PROJECT_ROOT / "data"))
    editions = tuple(
        e.strip()
        for e in os.environ.get("AUTO_BROADCAST_EDITIONS", "tech,ai,infosec").split(",")
        if e.strip()
    )
    return Config(
        app_port=int(os.environ.get("APP_PORT", "7777")),
        kokoro_url=os.environ.get("KOKORO_URL", "http://localhost:8880"),
        retention_days=int(os.environ.get("RETENTION_DAYS", "14")),
        default_voice=os.environ.get("DEFAULT_VOICE", "af_heart"),
        include_sponsors=_env_bool("INCLUDE_SPONSORS", True),
        # British male against the American-female default voice — distinct on accent *and*
        # register, so the switch into an ad is unmistakable even at 2x playback speed.
        sponsor_voice=os.environ.get("SPONSOR_VOICE", "bm_george"),
        max_concurrent_synth=int(os.environ.get("MAX_CONCURRENT_SYNTH", "4")),
        data_dir=data_dir,
        plex_url=os.environ.get("PLEX_URL", "").strip(),
        plex_token=os.environ.get("PLEX_TOKEN", "").strip(),
        auto_broadcast_time=os.environ.get("AUTO_BROADCAST_TIME", "").strip(),
        auto_broadcast_editions=editions,
        auto_broadcast_retry_hours=float(os.environ.get("AUTO_BROADCAST_RETRY_HOURS", "4")),
        ntfy_url=os.environ.get("NTFY_URL", "").strip(),
    )


config = load_config()
