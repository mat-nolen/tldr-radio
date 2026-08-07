"""Optional Plex guard: is anything streaming right now?

Used to warn before a manual broadcast, since Kokoro synthesis and Plex transcoding
both compete for the CPU on the plexbox. Disabled unless PLEX_URL + PLEX_TOKEN are set.
Failure to reach Plex is treated as "unknown" — it never blocks a broadcast.
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)


async def now_playing(plex_url: str, token: str, timeout: float = 4.0) -> dict | None:
    """Return {playing, count, sessions:[{title,user}]} or None if unconfigured/unreachable."""
    if not plex_url or not token:
        return None
    url = plex_url.rstrip("/") + "/status/sessions"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                url, params={"X-Plex-Token": token}, headers={"Accept": "application/json"}
            )
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("plex: could not read sessions from %s: %s", url, exc)
        return None

    container = data.get("MediaContainer", {}) if isinstance(data, dict) else {}
    metadata = container.get("Metadata") or []
    sessions = []
    for item in metadata:
        if item.get("type") == "episode":
            show = item.get("grandparentTitle", "")
            ep = item.get("title", "")
            title = f"{show} — {ep}".strip(" —") or "episode"
        else:
            title = item.get("title") or "something"
        user = (item.get("User") or {}).get("title", "")
        sessions.append({"title": title, "user": user})
    return {"playing": len(sessions) > 0, "count": len(sessions), "sessions": sessions}
