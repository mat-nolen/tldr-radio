"""Audio-integrity QA: does every chapter's displayed time match the real audio?

Three independent measurements per chapter must agree:
  1. DB `duration_seconds` (what the UI shows), set at synth time via mutagen
  2. ffprobe on the actual mp3 file (independent tool)
  3. the browser's decoded `audio.duration` (Chromium) for a sample episode
"""

import json
import subprocess

import httpx
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:7777"
AUDIO_DIR = "data/audio"
TOL = 0.3  # seconds


def ffprobe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=nk=1:nw=1", path],
        capture_output=True, text=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return None


def fmt(s):
    s = max(0, round(s or 0))
    return f"{s // 60}:{s % 60:02d}"


def integrity():
    rows = []
    eps = httpx.get(f"{BASE}/api/episodes", timeout=10).json()
    for e in eps:
        chs = httpx.get(f"{BASE}/api/episodes/{e['id']}", timeout=10).json()["chapters"]
        sum_ff = 0.0
        bad = []
        for c in chs:
            path = f"{AUDIO_DIR}/{e['id']}/{c['idx']}.mp3"
            ff = ffprobe(path)
            db = c["duration_seconds"]
            if ff is None:
                bad.append({"idx": c["idx"], "issue": "file/ffprobe missing"})
                continue
            sum_ff += ff
            if db is None or abs(ff - db) > TOL:
                bad.append({"idx": c["idx"], "ffprobe": round(ff, 2), "db": db})
        ep_total = e["duration_seconds"] or 0
        rows.append({
            "ep": e["id"], "title": e["title"], "chapters": len(chs),
            "sum_ffprobe": round(sum_ff, 1), "ep_total_db": round(ep_total, 1),
            "sum_vs_total_ok": abs(sum_ff - ep_total) <= max(1.0, 0.02 * ep_total),
            "chapter_mismatches": bad,
        })
    return rows


def browser_check(ep_id):
    out = {"ep": ep_id, "console_errors": [], "chapters": [], "app_playback": {}}
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=[
            "--autoplay-policy=no-user-gesture-required", "--mute-audio"])
        page = b.new_context().new_page()
        page.on("console", lambda m: out["console_errors"].append(m.text[:160])
                if m.type == "error" else None)
        page.goto(f"{BASE}/player.html?ep={ep_id}", wait_until="load")
        page.wait_for_selector("#chapters .chapter", timeout=10000)
        chs = httpx.get(f"{BASE}/api/episodes/{ep_id}", timeout=10).json()["chapters"]

        for c in chs:
            idx = c["idx"]
            row = page.query_selector(f'#chapters .chapter[data-idx="{idx}"]')
            clip_el = row.query_selector(".side .mono:not(.muted)")
            displayed = clip_el.inner_text().strip() if clip_el else None
            # Independent browser decode of the same file:
            decoded = page.evaluate(
                """(url) => new Promise(res => {
                    const a = new Audio(url);
                    a.addEventListener('loadedmetadata', () => res(a.duration));
                    a.addEventListener('error', () => res(null));
                })""",
                f"{BASE}/api/audio/{ep_id}/{idx}",
            )
            out["chapters"].append({
                "idx": idx,
                "displayed": displayed,
                "db": c["duration_seconds"],
                "browser_decoded": round(decoded, 2) if decoded else None,
                "match": decoded is not None and displayed == fmt(decoded),
            })

        # Exercise the real player on chapter 0: play, confirm LED advances + total matches
        page.click('#chapters .chapter[data-idx="0"]')
        page.wait_for_timeout(1800)
        led_cur = page.eval_on_selector("[data-led]", "e=>e.textContent")
        led_total = page.eval_on_selector(
            ".time-readout .total", "e=>e.textContent").replace("/", "").strip()
        out["app_playback"] = {
            "led_current_advanced": led_cur != "0:00",
            "led_current": led_cur,
            "led_total": led_total,
            "chapter0_displayed": out["chapters"][0]["displayed"],
            "total_matches_chapter0": led_total == out["chapters"][0]["displayed"],
        }
        b.close()
    return out


data = {"integrity": integrity()}
# pick the episode with the most chapters for the browser cross-check
biggest = max(data["integrity"], key=lambda r: r["chapters"])
data["browser"] = browser_check(biggest["ep"])

# ---- verdict ----
all_integrity_ok = all(
    not r["chapter_mismatches"] and r["sum_vs_total_ok"] for r in data["integrity"]
)
all_browser_ok = (
    not data["browser"]["console_errors"]
    and all(c["match"] for c in data["browser"]["chapters"])
    and data["browser"]["app_playback"]["led_current_advanced"]
    and data["browser"]["app_playback"]["total_matches_chapter0"]
)
data["VERDICT"] = {"integrity_ok": all_integrity_ok, "browser_ok": all_browser_ok}
print(json.dumps(data, indent=2))
