"""Full QA pass for TLDR Radio — drives the UI with Playwright + sweeps the audio API."""

import json
import os
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:7777"
OUT = "qa"
DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
R = {
    "console_errors": [],
    "page_errors": [],
    "request_failed": [],
    "pages": {},
    "episodes": [],
    "playback": {},
    "audio_sweep": [],
    "audition": {},
    "contrast": {},
    "contrast_below_aa": [],
    "mobile": {},
    "issues": [],
}


def note(msg):
    R["issues"].append(msg)


SET_THEME_JS = """
(t) => {
  document.documentElement.setAttribute('data-theme', t);
  try { localStorage.setItem('tldr-theme', t); } catch (e) {}
  window.dispatchEvent(new CustomEvent('themechange'));
}
"""

# Measures what the pixel actually is: the element's rendered colour against its
# composited ancestor background. Handles rgb()/rgba() and the color(srgb ...) that
# color-mix() computes to.
CONTRAST_JS = r"""
(sel) => {
  const el = document.querySelector(sel);
  if (!el) return null;
  const parse = s => {
    const n = (s || '').match(/-?\d*\.?\d+/g);
    if (!n || n.length < 3) return null;
    const k = s.startsWith('color(') ? 1 : 255;
    return [n[0] / k, n[1] / k, n[2] / k, n.length > 3 ? +n[3] : 1].map(Number);
  };
  const lin = v => (v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4));
  const lum = c => 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2]);
  const over = (fg, bg) => [0, 1, 2].map(i => fg[i] * fg[3] + bg[i] * (1 - fg[3])).concat([1]);
  const stack = [];
  for (let n = el; n; n = n.parentElement) {
    const c = parse(getComputedStyle(n).backgroundColor);
    if (c && c[3] > 0) { stack.push(c); if (c[3] >= 0.999) break; }
  }
  let bg = [1, 1, 1, 1];
  for (let i = stack.length - 1; i >= 0; i--) bg = over(stack[i], bg);
  const cs = getComputedStyle(el);
  let fg = parse(cs.color) || [0, 0, 0, 1];
  if (fg[3] < 1) fg = over(fg, bg);
  const a = lum(fg), b = lum(bg);
  const fs = parseFloat(cs.fontSize);
  const bold = (parseInt(cs.fontWeight, 10) || 400) >= 700;
  return {
    ratio: +(((Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)).toFixed(2)),
    large: fs >= 24 || (bold && fs >= 18.66),
  };
}
"""


def capture(page, name, selectors=()):
    """Screenshot + contrast-audit a page in BOTH themes.

    Reviewing dark-theme screenshots only is exactly how the Daytime chapter list
    (#1C1712 ink on the #241E17 faceplate — 1.05:1) shipped through a full QA pass,
    three phases and a deploy. Anything under 3:1 is unreadable and gets flagged;
    3:1-4.5:1 is recorded as `contrast_below_aa` without failing the run, so the
    approved palette's muted metadata stays visible without becoming noise.
    """
    for theme in ("light", "dark"):
        page.evaluate(SET_THEME_JS, theme)
        page.wait_for_timeout(200)
        page.screenshot(path=f"{OUT}/{name}-{theme}.png", full_page=True)
        for sel in selectors:
            got = page.evaluate(CONTRAST_JS, sel)
            if not got:
                continue
            key = f"{name}/{theme} {sel}"
            R["contrast"][key] = got["ratio"]
            floor = 3.0 if got["large"] else 4.5
            if got["ratio"] < 3.0:
                note(f"contrast {key}: {got['ratio']}:1 — text is effectively invisible")
            elif got["ratio"] < floor:
                R["contrast_below_aa"].append(f"{key} {got['ratio']}:1 (AA needs {floor})")


def audio_sweep():
    eps = httpx.get(f"{BASE}/api/episodes", timeout=10).json()
    for e in eps:
        detail = httpx.get(f"{BASE}/api/episodes/{e['id']}", timeout=10).json()
        chs = detail["chapters"]
        missing = [c["idx"] for c in chs if not c["audio_path"]]
        idxs = sorted({chs[0]["idx"], chs[len(chs) // 2]["idx"], chs[-1]["idx"]})
        bad = []
        for idx in idxs:
            r = httpx.get(
                f"{BASE}/api/audio/{e['id']}/{idx}",
                headers={"Range": "bytes=0-2047"},
                timeout=10,
            )
            if r.status_code not in (200, 206):
                bad.append([idx, r.status_code])
        R["audio_sweep"].append({
            "ep": e["id"], "title": e["title"], "status": e["status"],
            "chapters": len(chs), "missing_audio_path": missing, "audio_http_bad": bad,
        })
        if e["status"] != "ready":
            note(f"ep{e['id']} status={e['status']} (not ready)")
        if missing:
            note(f"ep{e['id']} missing audio_path for chapters {missing}")
        if bad:
            note(f"ep{e['id']} audio endpoint returned non-2xx: {bad}")


def audition_qa():
    """Audition a *fresh* (uncached) voice under the browser's real autoplay policy.

    Deliberately does NOT pass --autoplay-policy=no-user-gesture-required: the bug this guards
    against was play() being rejected because the click's user-activation window had already
    expired during a slow first synth. With autoplay forced on, that can never reproduce.
    """
    try:  # /api/voices proxies Kokoro; with it down this used to raise and kill the report
        resp = httpx.get(f"{BASE}/api/voices", timeout=10)
        resp.raise_for_status()
        voices = resp.json()["voices"]
    except Exception as exc:
        note(f"audition: /api/voices unusable ({type(exc).__name__}) — is Kokoro up?")
        R["audition"] = {"skipped": "voices unavailable"}
        return
    voices = [v if isinstance(v, str) else (v.get("id") or v.get("name")) for v in voices]
    voice = voices[-1] if voices else None
    if not voice:
        note("audition: no voices reported by /api/voices")
        return
    # Force the slow path — synthesis on first request is exactly what broke playback.
    sample = DATA_DIR / "audio" / "auditions" / f"{voice}.mp3"
    if sample.exists():
        sample.unlink()
    else:
        R["audition"]["precached"] = False

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--mute-audio"])
        page = b.new_context(viewport={"width": 1280, "height": 900}).new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text[:200]) if m.type == "error" else None)
        page.goto(BASE + "/settings.html", wait_until="load")
        page.wait_for_selector(f'.audition[data-voice="{voice}"]', timeout=15000)
        page.click(f'.audition[data-voice="{voice}"]')
        try:  # first synth is seconds of CPU, fetched before the element ever sees a src
            # `dataset.voice` is the discriminator, and it is load-bearing: the click also plays
            # 8 ms of silence to unlock the element, and that clip alone satisfies
            # "!paused && currentTime > 0". Without this the stage passes on the unlock and
            # never notices the real audition failed.
            page.wait_for_function(
                "(v) => { const a = document.getElementById('audition-audio');"
                " return a && a.dataset.voice === v && !a.paused && a.currentTime > 0"
                " && !a.error; }",
                arg=voice,
                timeout=45000,
            )
            playing = True
        except Exception:
            playing = False
        state = page.evaluate(
            "() => { const a = document.getElementById('audition-audio');"
            " return a ? {paused: a.paused, t: a.currentTime, voice: a.dataset.voice || null,"
            " blob: (a.currentSrc || '').startsWith('blob:'),"
            " err: a.error && a.error.code} : null; }"
        )
        surfaced = page.evaluate(
            f"() => !!document.querySelector('.audition[data-voice=\"{voice}\"][data-failed]')"
        )
        R["audition"] = {
            "voice": voice, "playing": playing, "state": state,
            "error_surfaced": surfaced, "console_errors": errors,
        }
        if not playing and not surfaced:
            note(f"audition({voice}): played nothing AND surfaced no error — silent-failure bug")
        elif not playing:
            note(f"audition({voice}): did not play, but the failure was surfaced: {errors}")
        b.close()


def browser_qa():
    with sync_playwright() as p:
        b = p.chromium.launch(
            headless=True,
            args=["--autoplay-policy=no-user-gesture-required", "--mute-audio"],
        )
        page = b.new_context(viewport={"width": 1280, "height": 900}).new_page()
        page.on("console", lambda m: R["console_errors"].append(f"{m.type}: {m.text}"[:200])
                if m.type == "error" else None)
        page.on("pageerror", lambda e: R["page_errors"].append(str(e)[:200]))
        page.on("requestfailed", lambda r: R["request_failed"].append(r.url[:160]))

        # ---- Desk ----
        page.goto(BASE, wait_until="load")
        # Stations are rendered from /api/editions, so "load" fires before they exist.
        try:
            page.wait_for_selector(".station", timeout=10000)
        except Exception:
            note("desk: stations did not render from /api/editions")
        R["pages"]["desk"] = {
            "stations": len(page.query_selector_all(".station")),
            "stations_pressed": len(page.query_selector_all('.station[aria-pressed="true"]')),
            "broadcast_btn": bool(page.query_selector("#broadcast")),
            "date_default": page.eval_on_selector("#date", "e=>e.value"),
            "queue_present": bool(page.query_selector("#queue")),
        }
        page.click("[data-theme-toggle]")
        R["pages"]["desk"]["theme_after_toggle"] = page.evaluate(
            "document.documentElement.getAttribute('data-theme')")
        capture(page, "desk", [".station", ".eyebrow", ".wordmark", ".mast-nav a"])

        # ---- Settings ----
        page.goto(BASE + "/settings.html", wait_until="load")
        try:
            page.wait_for_selector("#voice-list .voice-row", timeout=10000)
        except Exception:
            note("settings: voice list did not populate")
        try:
            page.wait_for_selector("#station-list .st-row", timeout=10000)
        except Exception:
            note("settings: station list did not populate")
        desk_boxes = page.query_selector_all('#station-list input[data-kind="desk"]')
        auto_boxes = page.query_selector_all('#station-list input[data-kind="auto"]')
        R["pages"]["settings"] = {
            "voice_rows": len(page.query_selector_all("#voice-list .voice-row")),
            "groups": len(page.query_selector_all("#voice-list .voice-group")),
            "default_checked": bool(page.query_selector('input[name="voice"]:checked')),
            "station_rows": len(page.query_selector_all("#station-list .st-row")),
            "desk_boxes": len(desk_boxes),
            "auto_boxes": len(auto_boxes),
            "estimate": page.eval_on_selector("#station-estimate", "e=>e.textContent"),
        }
        if len(desk_boxes) != len(auto_boxes):
            note("settings: Desk and Auto checkbox counts disagree")
        # Auto implies Desk — ticking Auto on a row that is off must switch Desk on with it.
        off = page.query_selector('#station-list input[data-kind="desk"]:not(:checked)')
        if off:
            slug = off.get_attribute("data-slug")
            page.check(f'#station-list input[data-kind="auto"][data-slug="{slug}"]')
            if not page.is_checked(f'#station-list input[data-kind="desk"][data-slug="{slug}"]'):
                note(f"settings: ticking Auto for {slug} left Desk unchecked")
            # Clearing Desk must clear Auto back — and leaves the form as we found it.
            page.uncheck(f'#station-list input[data-kind="desk"][data-slug="{slug}"]')
            if page.is_checked(f'#station-list input[data-kind="auto"][data-slug="{slug}"]'):
                note(f"settings: clearing Desk for {slug} left Auto checked")
        # Sponsor reads. The note is asserted on, not just measured: it is the only thing that
        # explains why the Library doesn't change after saving, and losing it is a silent
        # regression that no other check would catch.
        sponsor_opts = len(page.query_selector_all("#sponsor-voice option"))
        R["pages"]["settings"]["sponsor_toggle"] = bool(page.query_selector("#include-sponsors"))
        R["pages"]["settings"]["sponsor_voice_options"] = sponsor_opts
        R["pages"]["settings"]["sponsor_note"] = page.eval_on_selector(
            "#sponsor-note", "e=>e.textContent.trim()")[:120]
        if not sponsor_opts:
            note("settings: sponsor voice dropdown did not populate")
        if sponsor_opts and sponsor_opts != len(page.query_selector_all("#voice-list .voice-row")):
            note("settings: sponsor voice list and main voice list offer different voices")
        # The Audition button must follow the dropdown, or it auditions the wrong voice.
        if page.query_selector("#sponsor-voice") and sponsor_opts > 1:
            second = page.eval_on_selector("#sponsor-voice option:nth-child(2)", "e=>e.value")
            page.select_option("#sponsor-voice", second)
            if page.get_attribute("#sponsor-audition", "data-voice") != second:
                note("settings: sponsor Audition button did not follow the dropdown")
        if "next" not in R["pages"]["settings"]["sponsor_note"].lower():
            note("settings: sponsor note no longer says the change applies to the next broadcast")
        capture(page, "settings", [
            ".voice-name", ".voice-id", ".rule-label", ".audition",
            ".st-name", ".st-tag", ".st-desc", ".st-head span", "#station-estimate",
            ".sp-toggle span", ".sp-note", ".sp-voice select", ".sp-voice .eyebrow",
        ])

        # ---- Player: iterate every episode ----
        page.goto(BASE + "/player.html", wait_until="load")
        try:
            page.wait_for_selector("#library .day-toggle", timeout=10000)
        except Exception:
            note("player: library did not populate")
        day_groups = len(page.query_selector_all("#library .day-group"))
        # Days collapse by default, so cards start hidden — open every group before
        # driving them, or Playwright waits forever for an invisible card.
        page.eval_on_selector_all(
            "#library .day-toggle[aria-expanded='false']", "els=>els.forEach(e=>e.click())")
        collapsed_left = len(
            page.query_selector_all("#library .day-toggle[aria-expanded='false']"))
        if collapsed_left:
            note(f"library: {collapsed_left} day groups would not expand")
        ep_ids = page.evaluate(
            "[...document.querySelectorAll('#library .ep-card')].map(c=>c.dataset.ep)")
        # Delete control per card — presence only; QA must never destroy the real library.
        del_ids = page.evaluate(
            "[...document.querySelectorAll('#library .ep-del')].map(b=>b.dataset.del)")
        R["pages"]["player"] = {
            "library_count": len(ep_ids),
            "delete_buttons": len(del_ids),
            "day_groups": day_groups,
        }
        if sorted(del_ids) != sorted(ep_ids):
            note(f"library: {len(ep_ids)} episodes but {len(del_ids)} delete buttons")
        for ep_id in ep_ids:
            page.click(f'#library .ep-card[data-ep="{ep_id}"]')
            try:
                page.wait_for_selector("#chapters .chapter", timeout=10000)
            except Exception:
                note(f"episode {ep_id}: chapters did not render")
                continue
            sponsors = page.query_selector_all('#chapters .chapter[data-kind="sponsor"]')
            R["episodes"].append({
                "ep": ep_id,
                "title": page.eval_on_selector("#ep-title", "e=>e.textContent"),
                "chapters": len(page.query_selector_all("#chapters .chapter")),
                "dividers": len(page.query_selector_all("#chapters .rule-label")),
                "read_more_links": len(page.query_selector_all("#chapters a.readmore")),
                "sponsor_chapters": len(sponsors),
            })
            # A sponsor you can't see coming is one you can't skip — the pill is the whole
            # point of putting ads in the list rather than hiding them.
            if len(sponsors) != len(page.query_selector_all("#chapters .sponsor-pill")):
                note(f"episode {ep_id}: sponsor chapters without a Sponsor pill")
            if sponsors:
                sponsors[0].click()
                page.wait_for_timeout(300)
                if page.get_attribute("#now-chapter", "data-kind") != "sponsor":
                    note(f"episode {ep_id}: sponsor chapter did not mark the now-playing card")
                if page.eval_on_selector("#nc-section", "e=>e.textContent").strip() != "Sponsored":
                    note(f"episode {ep_id}: sponsor card is not labelled Sponsored")
                if page.eval_on_selector("#nc-pos", "e=>e.textContent").strip() != "SPONSOR":
                    note(f"episode {ep_id}: sponsor card shows a story position")

        # ---- Playback test on the first episode ----
        if ep_ids:
            page.click(f'#library .ep-card[data-ep="{ep_ids[0]}"]')
            page.wait_for_selector("#chapters .chapter")
            page.click(".icon-btn.primary")   # play
            page.wait_for_timeout(2500)
            R["playback"]["led_after_2.5s"] = page.eval_on_selector(
                "[data-led]", "e=>e.textContent")
            page.click('.transport .icon-btn >> nth=4')  # next chapter
            page.wait_for_timeout(500)
            R["playback"]["current_chapter_highlighted"] = bool(
                page.query_selector('#chapters .chapter[data-current="true"]'))
            # The now-playing card must track the highlighted chapter, not lag behind it.
            current_head = page.eval_on_selector(
                '#chapters .chapter[data-current="true"] .ch-head', "e=>e.textContent")
            card_head = page.eval_on_selector("#nc-title", "e=>e.textContent")
            # Download must be offered on a ready episode, point at the right route, and be
            # tappable — it exists to get audio onto a phone, so a sub-44px control is a bug.
            dl = page.query_selector("#ep-download:not([hidden])")
            if dl is None:
                note("player: no download control on a ready episode")
            else:
                box = dl.bounding_box() or {"height": 0}
                href = dl.get_attribute("href") or ""
                R["playback"]["download_href"] = href
                R["playback"]["download_height"] = round(box["height"])
                if "/download" not in href:
                    note(f"player: download control points at {href!r}")
                if box["height"] < 44:
                    note(f"player: download control is {round(box['height'])}px tall, under 44")
                head = httpx.get(f"{BASE}{href}", timeout=120, follow_redirects=True)
                cd = head.headers.get("content-disposition", "")
                R["playback"]["download_bytes"] = len(head.content)
                R["playback"]["download_disposition"] = cd
                if head.status_code != 200:
                    note(f"player: download returned HTTP {head.status_code}")
                elif "attachment" not in cd or ".mp3" not in cd:
                    note(f"player: download is not an attachment ({cd!r}) — it will play, not save")
                elif not head.content.startswith((b"ID3", b"\xff")):
                    note("player: download did not return mp3 data")

            R["playback"]["now_chapter_title"] = card_head
            if current_head != card_head:
                note(f"now-playing card shows {card_head!r}, chapter list {current_head!r}")
            page.select_option(".speed", index=4)  # 2.0x
            R["playback"]["speed_value"] = page.eval_on_selector(".speed", "e=>e.value")
            # Everything below #ep-title sits on the dark faceplate, which does NOT
            # follow the theme — the exact trap the Daytime bug fell into.
            capture(page, "player", [
                "#ep-title", "#ep-sub", "#ep-voice",
                "#nc-section", "#nc-title", "#nc-source", "#nc-meta", "#nc-link",
                "#chapters .rule-label", "#chapters .chapter h4", "#chapters .chapter .num",
                "#chapters .chapter summary", "#chapters a.readmore",
                ".sponsor-pill", '#chapters .chapter[data-kind="sponsor"] .ch-head',
                '#chapters .chapter[data-current="true"] h4',
                '#chapters .chapter[data-current="true"] .num',
                # Both day-header states: the resting one is a control and must read
                # like one; the playing one is the --signal accent (3.03:1 on paper by
                # design, same as .readmore / aria-current, not a regression).
                "#library .day-group:not([data-playing]) .day-date",
                "#library .day-group[data-playing] .day-date",
                "#library .ep-title", "#library .ep-meta",
            ])

        # ---- Responsive ----
        page.set_viewport_size({"width": 390, "height": 844})
        for path, name in ((BASE, "desk-mobile"), (BASE + "/player.html", "player-mobile")):
            page.goto(path, wait_until="load")
            page.wait_for_timeout(1500)
            # A shorthand in .page once ate .wrap's gutters and every page scrolled
            # sideways; one cheap comparison catches any horizontal overflow.
            over = page.evaluate(
                "() => [document.documentElement.scrollWidth,"
                " document.documentElement.clientWidth]")
            R["pages"].setdefault("responsive", {})[name] = {"scrollW": over[0], "clientW": over[1]}
            if over[0] > over[1]:
                note(f"{name}: horizontal overflow — scrollWidth {over[0]} > viewport {over[1]}")
            capture(page, name)
        b.close()


#: Real iPhone viewports. All are below the 640px masthead breakpoint; the iPad is above it and
#: is here to prove the phone rules do not bleed into the tablet layout.
PHONES = [
    ("iphone-se", 375, 667),
    ("iphone-14", 390, 844),
    ("iphone-15-pro-max", 430, 932),
    ("ipad-portrait", 768, 1024),
]
MIN_TAP = 44  # Apple HIG minimum touch target, in CSS px


def mobile_qa():
    """Drive the real pages at phone widths in WebKit — the engine an iPhone actually runs.

    This stage exists because a shipped bug got past every other one: the masthead hid all nav
    links except `[aria-current="page"]` below 640px, i.e. it kept only the link to the page you
    were already on. Every page was a dead end on a phone, and nothing here was looking at a
    viewport that narrow. Desktop-only QA cannot see a media query it never triggers.
    """
    with sync_playwright() as p:
        b = p.webkit.launch(headless=True)
        for label, w, h in PHONES:
            ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2,
                                is_mobile=True, has_touch=True)
            page = ctx.new_page()
            for path in ("index.html", "player.html", "settings.html"):
                page.goto(f"{BASE}/{path}", wait_until="load")
                page.wait_for_timeout(400)
                got = page.evaluate("""() => {
                  const links = [...document.querySelectorAll('.mast-nav a')];
                  const shown = links.filter(a => {
                    const r = a.getBoundingClientRect();
                    return getComputedStyle(a).display !== 'none' && r.width > 0 && r.height > 0;
                  });
                  const small = shown.filter(a => a.getBoundingClientRect().height < 44)
                                     .map(a => a.textContent.trim());
                  const de = document.documentElement;
                  return {
                    total: links.length,
                    shown: shown.map(a => a.textContent.trim()),
                    reachable: shown.filter(a => !a.hasAttribute('aria-current')).length,
                    smallTargets: small,
                    overflow: de.scrollWidth > de.clientWidth,
                  };
                }""")
                # An icon inside a stretched flex button can be shrunk to zero width and vanish
                # while still reporting visible — caught exactly that on the Download control.
                collapsed = page.evaluate("""() => {
                  const out = [];
                  for (const svg of document.querySelectorAll('button svg, a.btn svg')) {
                    const host = svg.closest('button, a');
                    if (!host || host.hasAttribute('hidden')) continue;
                    if (host.offsetParent === null) continue;
                    const r = svg.getBoundingClientRect();
                    if (r.height > 0 && r.width < 1) {
                      out.push((host.id || host.className || host.tagName).toString().slice(0, 40));
                    }
                  }
                  return out;
                }""")
                if collapsed:
                    note(f"mobile {label}/{path}: icons collapsed to zero width in {collapsed}")

                key = f"{label}/{path}"
                R["mobile"][key] = got
                # The actual regression: at least one link OFF this page must be tappable.
                if got["reachable"] < got["total"] - 1:
                    note(f"mobile {key}: only {got['shown']} reachable — "
                         f"{got['total'] - 1} off-page links expected, "
                         f"{got['reachable']} usable. Dead end.")
                if got["overflow"]:
                    note(f"mobile {key}: page scrolls sideways")
                if got["smallTargets"]:
                    note(f"mobile {key}: nav targets under {MIN_TAP}px: {got['smallTargets']}")

            # Transport must stay one row: Previous and Next are the two most-used controls and
            # wrapping split them across lines. Compare vertical CENTRES — the play button is
            # deliberately larger, so its top edge legitimately differs from its neighbours'.
            page.goto(f"{BASE}/player.html", wait_until="load")
            try:
                page.wait_for_selector(".transport .icon-btn", timeout=10000)
            except Exception:
                note(f"mobile {label}: transport never rendered")
                ctx.close()
                continue
            rows = page.evaluate("""() => {
              const c = [...document.querySelectorAll('.transport .icon-btn')]
                .map(b => { const r = b.getBoundingClientRect(); return r.top + r.height / 2; });
              return {centres: c, spread: Math.round(Math.max(...c) - Math.min(...c)),
                      count: c.length};
            }""")
            R["mobile"][f"{label}/transport"] = rows
            if rows["spread"] > 4:
                note(f"mobile {label}: transport buttons span {rows['spread']}px vertically "
                     f"— controls have wrapped onto separate rows")
            page.screenshot(path=f"{OUT}/mobile-{label}.png", full_page=False)
            ctx.close()
        b.close()


def audition_stall_qa():
    """The regression guard for the plexbox's silent audition (v0.8.1).

    Two variables had to combine, which is why it went un-reproduced for three deploys:
    **WebKit** (not Chromium) *and* a **slow first synth**. WebKit abandons a media load that
    stalls past ~16 s and reports MEDIA_ERR_SRC_NOT_SUPPORTED while still saying `paused: false`
    — pure silence. Chromium waits indefinitely, so no Chromium test can ever catch this.

    The stall is injected at the network layer rather than by actually slowing Kokoro, so the
    check is deterministic and runs in ~20 s on any machine. If someone ever puts the <audio>
    element back on the far side of the synth, this fails and the earlier stages still report.
    """
    stall_seconds = 20  # comfortably past WebKit's limit; the old code died here
    voice = "af_heart"
    with sync_playwright() as p:
        b = p.webkit.launch(headless=True)  # real WebKit media policy, no autoplay override
        page = b.new_context().new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text[:200]) if m.type == "error" else None)

        def stall(route):
            time.sleep(stall_seconds)
            route.continue_()

        page.route("**/api/voices/audition/**", stall)
        page.goto(BASE + "/settings.html", wait_until="load")
        page.wait_for_selector(f'.audition[data-voice="{voice}"]', timeout=15000)
        page.click(f'.audition[data-voice="{voice}"]')
        try:
            # Must match the REAL clip, not the 8 ms silent unlock — see audition_qa.
            page.wait_for_function(
                "(v) => { const a = document.getElementById('audition-audio');"
                " return a && a.dataset.voice === v && !a.paused && a.currentTime > 0"
                " && !a.error; }",
                arg=voice,
                timeout=(stall_seconds + 25) * 1000,
            )
            playing = True
        except Exception:
            playing = False
        state = page.evaluate(
            "() => { const a = document.getElementById('audition-audio');"
            " return a ? {paused: a.paused, t: a.currentTime, err: a.error && a.error.code,"
            " voice: a.dataset.voice || null,"
            " blob: (a.currentSrc || '').startsWith('blob:')} : null; }"
        )
        R["audition_stall"] = {
            "engine": "webkit", "stall_seconds": stall_seconds,
            "playing": playing, "state": state, "console_errors": errors,
        }
        if not playing:
            note(
                f"audition stall({stall_seconds}s, webkit): did not play — the media element is "
                f"back on the far side of the synth (state={state})"
            )
        b.close()


for stage in (audio_sweep, browser_qa, mobile_qa, audition_qa, audition_stall_qa):
    try:
        stage()
    except Exception as exc:  # one dead stage must not cost us every other result
        note(f"{stage.__name__} crashed: {type(exc).__name__}: {exc}")
print(json.dumps(R, indent=2))
