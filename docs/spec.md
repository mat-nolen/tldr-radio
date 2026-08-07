# TLDR Radio — Specification

> The full specification. Decisions recorded here are the ones the implementation actually
> follows; see `CLAUDE.md` for the short list of those that must not be undone.

---

## Project Overview

- **Name:** TLDR Radio
- **Purpose:** Turn the three daily **TLDR** newsletters (Tech, AI, Infosec) into chapterized
  audio episodes you *listen* to instead of read. Pick a day, hit Go, and a couple of minutes
  later there are episodes queued in the browser.
- **Motivation:** The headlines + 1–2 sentence summaries carry enough signal; reading them means
  sitting at a screen. The owner would rather listen.
- **Target user:** One person (the owner), on their own machine. Single-user, `localhost` only.
- **Scope — in:** local Docker site; TTS via Kokoro; archive fetch + paste/drop fallback; three
  separate episodes per day; web player with chapters; voice picker; async job queue with live
  progress; 14-day retention.
- **Scope — out:** no cloud, no accounts, no LAN exposure, no podcast RSS, no Gmail/IMAP, no LLM
  at runtime, no merging editions, the other 11 TLDR editions, cross-edition dedup (cut — §16).

### Hard constraints (owner's words)
- Runs on the owner's machine, **in Docker**, as a **local site** opened in a browser.
- **$0** — no subscriptions, no API keys, no paid services.
- Hit Go → audio → **queued up so he can listen**.

---

## Tech Stack

- **Language:** Python 3.13.
- **Backend:** FastAPI + `uvicorn[standard]`; background `asyncio` worker; SSE for live progress.
- **Fetch/parse:** `httpx`, `beautifulsoup4`, `lxml`.
- **Uploads:** `python-multipart`.
- **Storage:** stdlib `sqlite3` (5 small tables, **no ORM**); mp3 + page cache on a `/data` volume.
- **TTS:** **Kokoro-82M** in Docker via **Kokoro-FastAPI** (`ghcr.io/remsky/kokoro-fastapi-cpu`),
  OpenAI-compatible `POST /v1/audio/speech`, ~68 voice packs, native `linux/arm64` **CPU** build
  (no GPU passthrough in Docker on Apple Silicon). *Switched from `hwdsl2/kokoro-server` during
  Spike #0 — its arm64 build ships CUDA torch and crashes on Apple Silicon; see `lessons_learned.md`.*
- **Frontend:** vanilla HTML/CSS/JS. **No framework, no build step, no CDN dependencies.** Fonts
  self-hosted as woff2.
- **Optional:** host `ffmpeg` (`/opt/homebrew/bin/ffmpeg`) for chaptered `.m4a` export only.
- **Tests:** `pytest`.

### Runtime target (verified 2026-07-22)
Mac Studio, M3 Ultra, 96 GB RAM, 28 cores, 332 GB free · Docker 29.5.3, server arch `arm64/linux`
· port **7777** app, **8880** Kokoro. **No host dependency** — `docker compose up` and it runs.

---

## Architecture

```
docker-compose.yml            app + kokoro services, volumes
Dockerfile                    python:3.13-slim + deps
Makefile                      make up / make down / make logs
app/main.py                   FastAPI routes + SSE
app/worker.py                 asyncio job queue + lifecycle state machine
app/pipeline/fetch.py         tldr.tech archive client + disk cache
app/pipeline/parse.py         structure-driven story extraction
app/pipeline/ingest_file.py   fallback: .eml / html / pasted text
app/pipeline/script.py        deterministic TTS text-prep + section glue
app/pipeline/pronounce.py     pronunciation dictionary (plain, editable, grows)
app/pipeline/synth.py         Kokoro client, concurrency, per-chapter mp3
app/db.py                     SQLite schema + queries (parameterized)
app/retention.py              14-day prune job
app/static/index.html         date + edition selector, job queue
app/static/player.html        library + player
app/static/settings.html      voice picker, retention
app/static/styles/design-tokens.css   the design system (CSS custom props)
app/static/styles/base.css            component foundation
app/static/app.js             shared player + SSE + fetch logic
app/static/fonts/             self-hosted woff2 (Bricolage Grotesque, Newsreader, Space Mono)
tests/fixtures/               saved archive HTML for tech / ai / infosec, 2026-07-22
tests/test_parse.py           one case per edition + unknown-section + sponsor-drop
tests/test_script.py          pronunciation + stripping cases
data/                         mounted volume — episodes.db, mp3 files, page cache
```

**Two-container compose:** `app` (FastAPI + static UI, `:7777`) and `kokoro`
(`ghcr.io/remsky/kokoro-fastapi-cpu`, `:8880`; model is bundled in the image). Browser → `http://localhost:7777`.

### Data flow
`select (date + editions)` → one job per edition → `fetch` (cached to disk) → `parse`
(structure-driven) → `script` (deterministic text-prep + glue) → `synth` (per-chapter mp3, ≤4
concurrent) → `store` (SQLite rows + mp3 files) → `play` (web player).

---

## Core Features

### MVP (Phase 1 — everything below ships in v1)
1. **Date + edition selector** — date picker (defaults today), one station button per **enabled**
   edition. "Fetch today" → one job **per edition**. *(v0.8.0: all 14 tldr.tech newsletters are
   available; which ones appear here, and which run overnight, are two switches per newsletter in
   Settings → Stations. Catalog: `app/editions.py`. Ships enabled: tech/ai/infosec.)*
2. **Archive fetch + parse** — `GET tldr.tech/<edition>/<date>`, cached to disk; structure-driven
   parser; sponsors + web chrome dropped; **fails loudly on 0 stories**.
3. **Deterministic text-prep** — strip read-time suffixes/URLs/chrome; pronunciation dictionary;
   static section announcements ("glue"); templated intro/outro. No LLM, ever.
4. **Kokoro synthesis** — one mp3 **per chapter** (seek-free chapter skipping); ≤4 concurrent.
5. **Job queue + live progress** — `queued → fetching → parsing → scripting → synthesizing (n/m) →
   ready | failed`; live via SSE; failed jobs retain error and are retryable.
6. **Web player** — library grouped by date (3/day, newest first); chapter list; play/pause,
   prev/next chapter, ±15 s scrub; speed 0.75×–2.5×; auto-advance; resume position (SQLite);
   per-chapter "show source"; keyboard (space, ← →, `[` `]`).
7. **Voice picker** — all 54 voices grouped by language/gender; audition sample (cached); save
   default; per-episode override at generation time.
8. **Retention** — nightly prune of episodes, mp3s, cached HTML older than 14 days (configurable).
9. **Fallback ingest** — paste URL/text/HTML, or drop `.eml`/`.html`/`.txt`, for when the archive
   404s or changes shape.

### Explicitly NOT built
Cross-edition duplicate flag (cut, §16 brief) · merging editions · the other 11 editions · any LLM ·
LAN/remote access · auth.

---

## Data Models

Five tables, stdlib `sqlite3`, parameterized SQL throughout. (Verbatim from brief §17.)

```sql
CREATE TABLE episodes (
  id INTEGER PRIMARY KEY, edition TEXT NOT NULL, issue_date TEXT NOT NULL,
  title TEXT NOT NULL, source_url TEXT NOT NULL, voice TEXT NOT NULL,
  status TEXT NOT NULL,           -- queued|fetching|parsing|scripting|synthesizing|ready|failed
  error TEXT, story_count INTEGER NOT NULL DEFAULT 0, duration_seconds REAL,
  created_at TEXT NOT NULL, ready_at TEXT,
  UNIQUE (edition, issue_date)
);
CREATE TABLE chapters (
  id INTEGER PRIMARY KEY,
  episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  idx INTEGER NOT NULL, kind TEXT NOT NULL,      -- 'intro' | 'story' | 'outro'
  section TEXT, headline TEXT, summary_source TEXT, script_text TEXT NOT NULL,
  url TEXT, read_time TEXT, audio_path TEXT, duration_seconds REAL,
  UNIQUE (episode_id, idx)
);
CREATE TABLE playback (
  episode_id INTEGER PRIMARY KEY REFERENCES episodes(id) ON DELETE CASCADE,
  chapter_idx INTEGER NOT NULL, position_seconds REAL NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE settings ( key TEXT PRIMARY KEY, value TEXT NOT NULL );  -- default_voice, retention_days, playback_speed
CREATE TABLE page_cache ( url TEXT PRIMARY KEY, fetched_at TEXT NOT NULL, path TEXT NOT NULL );
```

**Parser output shape:** `[{section, headline, summary, url, read_time, is_sponsor}]`. Sponsors
(`(Sponsor)`) and web chrome dropped before scripting.

**Glue lines are not their own chapter** (brief §16): each section announcement is *prepended* to
the script of the first story in that section, so one chapter = one story. The UI renders section
names as non-clickable dividers.

---

## Design System

**Direction: "Broadcast Desk."** The subject is a desk radio that reads you the news, so the
identity comes from *broadcast hardware* — a warm oat-paper desk with a dark radio faceplate, one
signal-orange tube-glow accent, and three station colors for the editions. Deliberately **not** a
dark-mode dashboard, and deliberately **not** the generic cream-serif-terracotta editorial template.

**References / rationale:** mid-century desk radios (cream bakelite bodies, dark dial faces, amber
tuning glow, VU meters, LED segment readouts) crossed with newsletter/newsprint typography. No
external screenshot was supplied — direction chosen by the design lead and owned by the owner
("you decide").

### Color palette (exact hex — full set in `styles/design-tokens.css`)

**Daytime Paper (light, default)**

| Token | Hex | Role |
|---|---|---|
| `--paper` | `#EDE7D8` | app ground — warm oat |
| `--paper-2` | `#F6F1E6` | raised surface (panels, cards) |
| `--paper-3` | `#FBF8F0` | highest surface (inputs) |
| `--ink` | `#1C1712` | primary text (warm near-black) |
| `--ink-2` | `#4A4237` | secondary text |
| `--ink-3` | `#8A7F6C` | muted / metadata |
| `--rule` / `--rule-strong` | `#D6CBB4` / `#C2B49A` | hairline / emphasized borders |
| `--console` | `#241E17` | dark radio faceplate (header + transport) |
| `--signal` | `#E2571E` | tube-glow — playhead, primary CTA, "on air", focus |
| `--edition-tech` | `#1E6E64` | station: Tech (teal) |
| `--edition-ai` | `#2B57A6` | station: AI (broadcast blue) |
| `--edition-infosec` | `#8E2C33` | station: Infosec (oxblood) |
| `--edition-<slug>` | *(11 more)* | v0.8.0: one per newsletter, hues spread by max-min spacing on the OKLCH circle around these three anchors, avoiding the signal-orange band. All land 4.99–7.29:1 on their panel in both themes. Applied via a `--st` custom property, never a rule per slug. |
| `--ok` / `--warn` / `--err` | `#2E7D52` / `#B8801C` / `#B23A2E` | semantic |

**Night Broadcast (dark, dial-glow):** deep warm brown-black ground (`#161210`), brighter signal
(`#F26522`) and station colors so accents read as glow. Full override set in the tokens file.
No token here is a framework default (no Tailwind blue/slate, Bootstrap, or Material values).

### Typography — a deliberate trio
- **Display — Bricolage Grotesque** (700/800): masthead wordmark, episode titles, section headers.
  Editorial-yet-mechanical; carries the broadcast-signage voice.
- **Body — Newsreader** (400/500 + italic): summaries and reading text. A real on-screen reading
  serif — ties the audio back to its newsletter source.
- **Data — Space Mono** (400/700): timestamps, story counts, LED-style time readout, station band
  labels, keycap hints. Used with restraint; retro-mechanical, on-theme.

Type scale: 11 / 13 / 17 (body) / 19 / 24 / 32 / 44 px + a `clamp()` hero. Line height 1.62 body,
1.08 display. Self-hosted woff2 (no CDN) in production; system fallback stacks in the tokens file.

### Spacing, radius, elevation
4px spacing grid (`--space-1..9`). Crisp hardware radii (`2 / 5 / 9px`, pill only for lamps/chips).
Flat-first: hairline rules do most of the work; one soft shadow lifts the console/player.

### Signature element — the **tuner**
The player transport is a radio tuner: the scrubber is a **frequency dial** with tick marks and an
amber **needle** playhead; the now-playing waveform is a **VU-meter** drawn on `<canvas>` that reads
the CSS tokens (`getComputedStyle` → `--signal`) so it re-themes for free; time shows in an
**LED-segment** mono readout. The "ON AIR" lamp pulses while a job is synthesizing. On the index,
editions are **preset/station buttons** with their station color.

### Iconography
Inline SVG, stroke style, 2px weight, ~20px (transport controls 26px). No icon font, no CDN script.

---

## User Interface

Three screens, top-nav within a shared masthead. No sidebar — this is a small, focused tool.

**`index.html` — The Desk (select + queue)**
```
┌ TLDR RADIO ─────────────────── [ Daytime ◐ ]  Library ─┐   ← dark faceplate masthead
│                                                          │
│  TUNE IN                          Wed · Jul 22 2026      │
│  ┌ Date [ 2026-07-22 ▾ ]  ────────────────────────────┐ │
│  │  [x] TECH      teal    ●  on  │  preset/station      │ │
│  │  [x] AI        blue    ●  on  │  buttons (aria-pressed)│
│  │  [x] INFOSEC   oxblood ●  on  │                      │ │
│  └──────────────────────── [ ▶ BROADCAST ] signal btn ─┘ │
│                                                          │
│  SIGNAL / QUEUE                                          │
│  ● TLDR AI      synthesizing 6/18  ▓▓▓▓▓▓░░░░  ON AIR    │
│  ● TLDR Tech    ready  15 stories · 11:42                │
│  ● TLDR Infosec failed  "0 stories parsed" [Retry]       │
└──────────────────────────────────────────────────────────┘
```

**`player.html` — The Console (library + player)**
```
┌ masthead ────────────────────────────────────────────────┐
│ LIBRARY (rail)          │  NOW PLAYING (console)           │
│ Jul 22 ▾                │  TLDR AI — Jul 22   [af_heart]   │
│  ▸ TLDR AI  18 · 11:04  │  ══╪════ tuner dial · needle ═══ │
│  ▸ TLDR Tech 15 · 10:31 │  ▁▃▅▇▅▃▁ VU waveform (canvas)    │
│  ▸ Infosec  14 · 09:58  │  ⏮  ⏪15  ▶  15⏩  ⏭   00:42/11:04│
│ Jul 21 ▾                │  speed 1.25× ▾                   │
│  ...                    │  ── Headlines & Launches ──      │
│                         │  ▶ 1 OpenAI ships…  5 min  [src] │
│                         │    2 Anthropic…            [src] │
│                         │  ── Deep Dives & Analysis ──     │
│                         │    3 …                           │
└──────────────────────────────────────────────────────────┘
```

**`settings.html` — voice picker + retention:** 54 voices grouped by language/gender, each with an
**Audition** button (synthesizes a fixed sample line, plays inline, cached); "set default"; retention
days input (default 14).

**State catalog:** loading = meter/skeleton on cards; empty library = "No episodes yet — tune in on
the Desk" invitation; error = inline banner (`banner-err`) + retry; degraded parse = amber banner
(`banner-warn`, "Only N stories parsed, expected ~15"); hover/focus/active/disabled all styled;
`prefers-reduced-motion` respected; visible keyboard focus ring on every control.

**Responsive:** desk/console are two-column ≥900px, stack to one column below; body never scrolls
horizontally.

---

## API / External Interfaces

**Consumed**

| Service | Endpoint | Auth |
|---|---|---|
| TLDR archive | `GET https://tldr.tech/<edition>/<YYYY-MM-DD>` | none |
| Kokoro | `POST http://kokoro:8880/v1/audio/speech` `{model,input,voice,response_format:"mp3"}` | none |
| Kokoro | `GET http://kokoro:8880/v1/audio/voices` | none |

**Exposed** (localhost only)
```
GET  /  ·  /player  ·  /settings
POST /api/jobs                {edition[], date} → one job per edition
GET  /api/jobs                current queue
GET  /api/jobs/stream         SSE live progress
POST /api/jobs/{id}/retry
GET  /api/episodes            ·  GET /api/episodes/{id}  ·  DELETE /api/episodes/{id}
GET  /api/audio/{ep}/{idx}    mp3, Range-request capable
PUT  /api/playback/{ep}       {chapter_idx, position_seconds}
GET  /api/voices              proxied Kokoro voice list
POST /api/voices/audition     {voice} → sample mp3
GET  /api/settings  ·  PUT /api/settings
POST /api/ingest/paste  ·  POST /api/ingest/file
```

---

## Error Handling & Edge Cases

(Verbatim from brief §18.)

| Scenario | Handling | User feedback |
|---|---|---|
| Archive 404 | Fail fast, no retry | "TLDR AI hasn't published for Jul 22 yet." + **Load previous day** |
| Archive network error/timeout | Retry 3× exp backoff, then fail | "Couldn't reach tldr.tech." + **Retry** |
| **Parse yields 0 stories** | **Fail loudly**, keep cached HTML, never emit empty episode | "Parser found 0 stories — layout may have changed. Cached to `data/cache/…`" |
| Parse < 5 stories | Proceed but flag | Amber banner "Only 3 parsed, expected ~15" |
| Unknown section name | Proceed, pass name through | Silent, logged INFO |
| Kokoro unreachable | Retry 3×, then fail job | "TTS service is down — is the `kokoro` container running?" |
| Kokoro 4xx/5xx on one chapter | Fail that chapter only, continue | Chapter struck-through + **Retry chapter**; episode still playable |
| Duplicate job (edition+date ready) | Reject, offer regenerate | "Already have this one." + **Regenerate** |
| Saved voice missing | Fall back to default, warn | "Voice `xx_yyy` unavailable — used `af_heart`." |
| Disk full during synth | Abort job, keep partial | "Out of disk space." |
| Retention hits file in use | Skip, retry next run | Silent, logged WARN |

**Logging:** stdlib `logging` → stdout (Docker captures). INFO lifecycle, WARN degraded, ERROR with
stack traces. **Fallback-ingest hazards** to defend against: `<style>`/`<script>` leaking into
`get_text()`, hidden preheader text, `&nbsp;`/zero-width spacers, quoted-printable soft breaks.

---

## Security & Privacy

No auth (localhost, single user) · no secrets, no API keys, no PII · parameterized SQL throughout ·
uploaded files parsed **in-memory, never executed** · outbound requests only to `tldr.tech` and the
`kokoro` container. Nothing sensitive exists to redact.

---

## Testing Strategy

- **Framework:** `pytest`.
- **Parser:** one case per edition against saved 2026-07-22 fixtures — story counts match a hand
  count; sponsors dropped; section names correct; no subscribe block / footer / date stamp leaks.
- **Unknown-section resilience:** a fixture with a renamed section degrades, doesn't crash.
- **Loud failure:** deliberately broken HTML → job fails with clear error, does *not* produce a
  2-story episode.
- **Text-prep:** pronunciation + stripping cases (`test_script.py`).
- **Performance:** Spike #0 gate — Kokoro on arm64 CPU must beat ~1.5× realtime. **PASSED: 7.07×
  measured** (73 s audio in 10.3 s) via `kokoro-fastapi-cpu` on the Mac Studio, 2026-07-23.
  Synthesis ≤4 concurrent. Chapter skip is instant (separate mp3s).
- **End-to-end:** today + all three editions → 3 jobs reach `ready` → correct chapter counts → play
  through, skip chapters, change speed, reload → resume works.
- **Retention:** seed old data, set retention to 1 day, prune → old mp3s/rows gone, recent survive.
- **Restart:** `docker compose down && up` → library + audio survive.
- **Design gate:** no framework-default palette; matches the chosen direction.

---

## Development Phases

Build order from brief §10.

- **Phase 1 — Spike #0 (gates everything). ✅ SPEED PASSED 2026-07-23.** Ran Kokoro-82M via
  `kokoro-fastapi-cpu` on arm64 CPU → **7.07× realtime**. (Original `hwdsl2/kokoro-server` arm64
  image was broken — CUDA torch — so we switched; see `lessons_learned.md`.) Normalizer probe on
  `$1.2B`, `40%`, `GPT-4o`, `CVE-2026-1234` synthesized for review. **Deliverable done:** timing +
  sample mp3s in `spike/` — awaiting the owner's listen-approval before app code.
- **Phase 2 — Skeleton (local).** FastAPI app + SQLite schema + health checks, run **bare-metal**
  (`venv` + `uvicorn app.main:app --port 7777`) against a locally-reachable Kokoro (the
  `kokoro-fastapi-cpu` container on `:8880`). No app Dockerfile yet.
- **Phase 3 — Pipeline (local).** fetch → parse → text-prep → synthesize via a test route (no UI),
  validated against all three real editions.
- **Phase 4 — UI.** Apply the design system, then date/edition selector, job queue + live progress,
  voice picker.
- **Phase 5 — Player.** Library, chapters, transport, speed, resume, source toggle.
- **Phase 6 — Run end-to-end locally & verify.** Full happy path on the host (select 3 editions →
  3 episodes → play/skip/speed/resume/reload) plus retention + loud-failure checks. **This is the
  gate before containerizing.**
- **Phase 7 — Dockerize.** Only once the local run is clean: add the app `Dockerfile` +
  `docker-compose.yml` (app + kokoro), `make up`/`make down`, README, first-run experience.

**Dev workflow (locked):** run and test the app **bare-metal end-to-end first**; wrap it in Docker
only after the local run passes. Preview the static UI over a local HTTP server (e.g.
`python3 -m http.server`), never `file://` — the folder name has a space, which breaks `file://`
relative CSS/JS paths.

---

## Dependencies & Setup

```
# requirements.txt (unpinned; pin at implementation against what resolves)
fastapi
uvicorn[standard]
httpx
beautifulsoup4
lxml
python-multipart
pytest
```
No ORM, no frontend framework, no build step, no CDN.

**Config (env, all optional with defaults):** `APP_PORT=7777` · `KOKORO_URL=http://kokoro:8880` ·
`RETENTION_DAYS=14` · `DEFAULT_VOICE=af_heart` · `MAX_CONCURRENT_SYNTH=4`.

**Local dev (primary during build):**
```
docker run -d -p 8880:8880 ghcr.io/remsky/kokoro-fastapi-cpu   # Kokoro only, in Docker
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
KOKORO_URL=http://localhost:8880 uvicorn app.main:app --reload --port 7777
```
Then open `http://localhost:7777`. Iterate here until the full pipeline passes.

**Packaged run (after local verification):** `docker compose up` (or `make up`) brings up both
`app` and `kokoro`. First run pulls the Kokoro model (~320 MB) into its volume. Open
`http://localhost:7777`.

---

## Deployment & Distribution

Not distributed. Local-only, one machine, via `docker compose`. `Makefile` targets:
`make up`, `make down`, `make logs`. Optional host `ffmpeg` enables chaptered `.m4a` export.

---

## Risks & Assumptions

**Assumptions:** Docker Desktop running on the host; `tldr.tech` archive stays public,
date-addressable, server-rendered; Kokoro arm64 CPU beats realtime (**Spike #0 verified 7.07×**); TLDR
summaries remain well-formed sentences.

| Risk | Mitigation |
|---|---|
| Kokoro arm64 CPU too slow / broken in Docker VM (gates the design) | **Resolved in Spike #0:** `hwdsl2` arm64 image crashes (CUDA torch) → switched to `kokoro-fastapi-cpu`; measured **7.07× realtime**. Voicebox.app on host remains the deeper fallback. |
| Archive markup changes (no LLM repair) | Cache every fetch; fail loudly with story counts; paste/drop always available; parser is the only thing to retune |
| Archive not yet published for today | Detect 404, "not published yet", offer previous day |
| Kokoro mispronounces tech jargon | Pronunciation dictionary, seeded in the spike, grown on first real listen |

**Open questions:** none blocking. Design direction is decided (owner delegated); revisit only if
the owner dislikes the first rendered pass.

---

## Future Enhancements (Out of Scope for V1)

The other 11 TLDR editions · cross-edition duplicate flag (cut) · podcast RSS / LAN access · LLM
rewriting · merged "all-editions" episode.

---

## Success Criteria

Spike gate passes (**beats realtime — 7.07× measured**; sample quality pending owner listen) · parser matches hand counts on all three
fixtures with sponsors/chrome dropped · broken HTML fails loudly (no short episode) · one full
episode listened end-to-end with every mangled term added to the dictionary and re-confirmed ·
end-to-end 3-episode run plays/skips/speeds/resumes across reload · retention prune removes old,
keeps recent · survives `docker compose down && up` · **no framework-default palette**.

---

## Getting Started (Implementation Guide)

1. **Spike #0 first** — do not write app code until Kokoro's arm64 CPU speed is measured and a
   sample mp3 is approved. This gates the whole architecture.
2. **Skeleton next** — compose + FastAPI + SQLite schema + health checks.
3. **Pipeline before UI** — get fetch→parse→script→synth correct against real fixtures behind a test
   route; the parser is the single point of failure, so lock it down with tests.
4. **Design is decided** — apply `styles/design-tokens.css` + `styles/base.css` from the first line
   of UI; never start from framework defaults.
5. **Player last** — it's the payoff, but it depends on real episodes existing.

Key decisions already locked: no LLM at runtime · three separate episodes · per-chapter mp3 · Kokoro
in Docker · localhost only · $0. See the brief for the full record.
