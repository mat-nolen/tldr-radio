# Version History

## [v0.1.0] - 2026-07-23
- Initial project setup via `/start-python`
- Finalized `spec.md` from the portable brief; approved the "Broadcast Desk" design system
- Created project skeleton, kit docs, config (`pyproject.toml`, `requirements.txt`, `.gitignore`)
- Initialized git repository (no commit yet)

## [v0.1.1] - 2026-07-23
- Spike #0: found `hwdsl2/kokoro-server` arm64 image broken (CUDA torch); switched TTS engine to `ghcr.io/remsky/kokoro-fastapi-cpu`
- Measured **7.07× realtime** synthesis on the Mac Studio arm64 CPU (73 s audio in 10.3 s) — speed gate PASSED
- Produced sample mp3s (`spike/`); propagated the engine change through spec/README/quickstart/CLAUDE

## [v0.2.0] - 2026-07-23
- Phase 2 (Skeleton): FastAPI app boots; SQLite schema (5 tables) initializes; `/api/health` pings Kokoro (up); `/api/voices` proxies 68 voices; `/api/episodes` lists (empty)
- Implemented + validated the Kokoro client (`app/pipeline/synth.py`); typed stubs for fetch/parse/script/ingest/worker/retention (Phase 3+)
- Seeded pronunciation dictionary (`app/pipeline/pronounce.py`); config from env (`app/config.py`)
- venv + deps installed; `ruff` clean; 3 smoke tests pass

## [v0.3.0] - 2026-07-23
- Phase 3 (Pipeline): `fetch.py` (archive + on-disk cache, 404/retry-backoff), `parse.py` (structure-driven, fail-loud), `script.py` (intro/outro + section glue + pronunciation), `synth.py` (concurrent per-chapter)
- Saved real fixtures (tech/ai/infosec 2026-07-22); 21 tests pass — parse counts 15/17/16, sponsor-drop, unknown-section resilience, loud-failure, scripting
- Dev route `POST /api/dev/pipeline`; validated end-to-end (ai/2026-07-22 → 19 chapters → real mp3s, ~3.3× realtime @ concurrency 4)

## [v0.4.0] - 2026-07-23
- Phase 4a (backend): `JobQueue` worker (one job/edition) persists episodes+chapters to SQLite with SSE progress; full API — jobs/retry, episodes CRUD, Range audio, playback, settings, voice audition; `mutagen` durations. Validated: infosec/2026-07-22 → episode (16 stories/18 chapters/629s), audio 206 Range.
- Phase 4b (UI): moved the approved design system + pages into `app/static/`; FastAPI serves them; `index.html` wired (station selector, Broadcast → `POST /api/jobs`, live SSE queue with progress/retry); `settings.html` wired (voice picker grouped by language/gender, audition, save default, retention).

## [v0.5.0] - 2026-07-24
- Phase 5 (Player): `player.html` wired to real data — library from `/api/episodes` (grouped by date, edition badges, story count + duration), chapter list with section dividers + "show source" + read-more, now-playing header.
- Transport bound to a real `<audio>`: play/pause, prev/next chapter, ±15 s, speed 0.75–2.5×, auto-advance; the tuner needle + LED track playback and the dial seeks; resume persisted via `PUT /api/playback` (throttled) and restored on load; keyboard (space, ← →, `[` `]`).

## [v0.6.0] - 2026-07-24
- Phase 6 (retention + verify): implemented `app/retention.py` prune (episodes + audio dirs + cached HTML older than `retention_days`) + nightly `run_periodically` wired into the app lifespan; unit-tested (old pruned, recent survive) → 22 tests.
- Full local end-to-end verification: 3-edition run (tech/ai/infosec for 2026-07-23) → 3 episodes reach `ready` with correct chapter counts (16/17/17); duplicate edition+date skipped; not-published/0-story page fails loudly (no empty episode).

## [v0.6.1] - 2026-07-24
- Full Playwright QA harness (`qa/qa.py`): drives every page, clicks through all episodes, tests playback, checks console errors, responsive + theme, and sweeps the audio API for every episode (all 7 play, every chapter has audio).
- Fixes surfaced by QA: current chapter/episode highlight (`setAttribute` vs `toggleAttribute`); permanent "ON AIR" (added `[hidden]` reset); "TLDR Ai"→"TLDR AI" title-case; stale-render flash on episode switch; replaced the distracting roving VU animation with a calm progress waveform that fills with playback.

## [v0.6.2] - 2026-07-24
- Clarified chapter timing: each chapter row now shows its **audio clip length** (mm:ss) alongside a clearly-labeled **"N min read"** (the TLDR article read-time — not the audio). The now-playing header shows "{stories} · {total} total" to tie back to the library. Resolves the confusion where a "9 minute" article played as a ~20s clip (that's the point — a long read condensed to a short listen).
- Reframed the article read-time as "· saves ~Nm"; added `Cache-Control: no-cache` so browsers stop serving stale assets.

## [v0.7.0] - 2026-07-25
- Phase 7 (Dockerize): `Dockerfile` (python:3.13-slim, multi-arch — builds amd64 on the plexbox), `docker-compose.yml` (app + `kokoro-fastapi-cpu`), `Makefile`, `.env.example`, `.dockerignore`. Split runtime deps from `requirements-dev.txt` (no pytest/playwright in the image → 65 MB app image).
- Self-hosted fonts (no CDN): 15 woff2 in `app/static/fonts/` + generated `styles/fonts.css`; removed the Google Fonts links.
- Overnight auto-broadcast (`AUTO_BROADCAST_TIME` + `TZ`) queues all editions daily. Optional Plex guard (`PLEX_URL` + `PLEX_TOKEN`) warns before a manual broadcast if Plex is streaming (shared CPU).
- Verified the full stack in Docker: build → both containers up → app↔kokoro health → UI/fonts/APIs serve → in-container lxml → a real broadcast synthesized to the mounted `/data`. README rewritten with plexbox install steps + resource guidance.

## [v0.7.1] - 2026-07-25
- Ops hardening from the plexbox handoff:
  - compose: Kokoro + app **healthchecks**, `depends_on: { kokoro: service_healthy }` ordered startup, `KOKORO_CPUS` **CPU cap** on Kokoro, `json-file` **log rotation** on both services.
  - auto-broadcast now **waits for completion**, records the outcome (`GET /api/auto-broadcast/status`), and optionally posts to `NTFY_URL` (failure-aware).
  - `GET /api/parser/health` — live per-edition story counts (no synth) to catch a `tldr.tech` layout change early.

## [v0.7.2] - 2026-07-25
- Prod bugs from the plexbox handoff, reproduced and fixed:
  - **Weekend/holiday runs no longer fail loudly.** Discovered live: `tldr.tech/<edition>/<date>` does *not* 404 on a day with no issue — it answers 200 and **redirects** to `/<edition>` (or `/`), serving the landing page (all 3 editions, Sat 2026-07-25; also pre-archive dates). `fetch.py` now compares the **final URL** to `/<edition>/<date>`; a redirect away → `NotPublishedError`, **never cached**. Jobs end in a new terminal **`skipped`** state ("not published", amber chip + *Check again*) instead of `failed`. `EmptyParseError` stays reserved for a real dated issue that parses to 0 stories — the genuine layout alarm. Already-poisoned cache entries self-heal (no dated `<h1>` and no story `<article>` → discard + re-fetch); verified by re-fetching the 458 KB junk entry cached for `tech-2027-01-01`.
  - **Auto-broadcast re-tries until it lands:** anything not yet successful — not-published *or* failed — is re-queued in place (same job/card) every ~25 min ± 5 min for `AUTO_BROADCAST_RETRY_HOURS` (default 4). Only the final state is reported, so a run that recovers sends no failure push, and a day where nothing publishes sends no notification at all. `GET /api/auto-broadcast/status` gains `attempts` + per-edition `note`.
  - **Fixed a pre-existing retry collision** (surfaced by making retries automatic; the manual Retry button hits it today): `episodes` has `UNIQUE (edition, issue_date)` and a job that dies during synthesis has already persisted its row, so a re-run raised `sqlite3.IntegrityError: UNIQUE constraint failed` and masked the original error. `_process` now adopts an already-`ready` episode as-is and discards a partial one (row + mp3s) before rebuilding. Verified live by stopping the Kokoro container mid-broadcast, then retrying → one clean `ready` episode (16/16 chapters).
  - **Voice audition no longer fails silently:** new `GET /api/voices/audition/{voice}`; playback starts synchronously inside the click so the `<audio>` element owns the slow first synth; failures surface a reason (the handler probes the URL and reports e.g. "HTTP 500"). The old code awaited a fetch then called `play()` in a `try/finally` with **no `catch`**. (The handoff's "expired user-activation window" cause did not hold up — Chrome gates media on *sticky* activation, which never expires; the plexbox's real error will now name itself in the tooltip.)
  - **Episodes are deletable from the Library** — an ✕ per card, confirm dialog, removes rows + mp3s; clears the console and re-selects if the deleted episode was playing. Cached source page is kept so a re-broadcast needs no re-fetch.
- Tests 22 → 46: `tests/test_fetch.py` (13 — redirect/homepage/trailing-slash, no-cache-on-landing, poisoned-cache self-heal, "0 stories stays loud"), `tests/test_auto_broadcast.py` (8 — re-tries until it lands, per-edition independence, quiet weekend, transient vs persistent failure) and `tests/test_worker_retry.py` (2 — retry after a synthesis failure yields one ready episode; re-run of a ready episode is a no-op). Ruff clean; full `qa/qa.py` pass with zero issues, plus a new audition check that runs under the browser's real autoplay policy.

## [v0.7.3] - 2026-08-01
- Player UI, from listening to it on the box:
  - **The Library rail collapses per day.** Each issue date is a toggle carrying the date, an
    episode count and a colour dot per edition, so a collapsed day still says what is inside.
    Days start **collapsed** — 14 days × 3 editions now fit on one screen instead of a long
    scroll — and the days you open are remembered in `localStorage` (`tldr-open-days`, expanded
    dates only, pruned to what retention still holds). The day holding whatever is on the console
    is tinted signal-orange, so a fully collapsed rail still shows where you are.
  - **The playing story is pinned under the transport.** A card between the transport row and the
    chapter list shows the section, `STORY n / total`, the headline, the full source summary,
    clip length + time saved, and the read-more link. It follows every chapter change — click,
    prev/next, keyboard, auto-advance — so you no longer scroll the list to see what is being read.
    Intro/outro degrade to just a label. The chapter list is unchanged below it.
- **Fixed: the entire chapter list was invisible in Daytime theme.** Chapters sit on the dark
  console faceplate but took the paper ink scale, which inverts with the theme while `--console`
  does not — headlines rendered #1C1712 on #241E17, about **1.05:1**. Every QA screenshot had been
  taken in Night theme, so it was never seen. `.console` now carries its own ink scale in `base.css`
  (headings, `.muted`, `.rule-label`), and the current-chapter row mixes its tint against the
  faceplate instead of using `--signal-tint`, which flips to a pale wash in Daytime.
- **Fixed: every page ran flush to the screen edge and scrolled sideways on a phone.** `.page` set
  `padding` as a shorthand and silently zeroed the `.wrap` gutters it always rides on; now
  `padding-block`. Below 640px the chapter row also drops its timing/read-more column onto its own
  line — both are `nowrap` and could not fit beside a headline, forcing the page 6px wider than the
  viewport. Verified: no horizontal overflow on Desk/Player/Settings at 390/768/1280px.
- `qa/qa.py` hardened against the two classes of bug it had just missed:
  - **Captures every page in both themes** (`<page>-light.png` / `<page>-dark.png`) and, because a
    screenshot only helps if someone looks at it, **measures real contrast** — composited ancestor
    background vs rendered colour, handling `rgb()`/`rgba()`/`color(srgb …)`. Under 3:1 is an
    issue; 3–4.5:1 is recorded as `contrast_below_aa` without failing the run, so the approved
    palette's muted metadata is visible without becoming noise. Chapter headlines now measure
    **13.37:1** in Daytime (was 1.05:1).
  - **Asserts `scrollWidth <= clientWidth`** at 390px on both mobile pages — one cheap check that
    catches any horizontal overflow, which screenshot review reliably does not.
  - **A dead stage no longer costs every other result:** stages run in a loop that records a crash
    as an issue, and `audition_qa` degrades with a clear "is Kokoro up?" note instead of raising an
    uncaught `JSONDecodeError` before the report ever printed.
  - Expands the day groups before driving the library (cards start hidden), reports `day_groups`,
    and asserts the now-playing card matches the highlighted chapter.
- The audit immediately caught a regression in the new control: the day header had been styled
  `--ink-3` (3.0:1) as if it were still a passive label, when collapsing made it the only way into
  a day. Now `--ink-2` — **8.01:1**.
- 46 tests, ruff clean, full `qa/qa.py` (all three stages, Kokoro up) with **zero issues**.

## [v0.8.0] - 2026-08-06
**All 14 TLDR newsletters, switchable in Settings.** The app shipped with `tech`, `ai` and
`infosec` hardcoded in eight places; they are now one catalog entry each.
- **`app/editions.py` is the single declaration site** — slug, display name and tagline for all 14
  newsletters tldr.tech publishes (`tech`, `dev`, `ai`, `infosec`, `product`, `devops`, `founders`,
  `design`, `marketing`, `crypto`, `fintech`, `it`, `data`, `hardware`). Taglines name each
  edition's own first three sections, verified by fetching and parsing a real issue of every one —
  the same way the original three were written. `EDITION_NAMES` is now a view onto it.
- **The parser needed no changes.** It keys on structure, not a section list, so the 11 new
  editions parse unchanged — proven by three new fixtures (`dev`, `marketing`, `hardware`) with
  hand-verified counts, and end-to-end by broadcasting a real `dev` issue (13 stories, 15 chapters,
  330 s) through fetch → parse → script → synth.
- **Each edition carries two strings that answer different questions:** `tagline` (its own first
  three section names — what an episode contains, in running order) and `description` (tldr.tech's
  own one-liner — who the newsletter is *for*, which is what you need when deciding to switch it
  on). The Settings table shows both; the Desk station chips show the tagline.
- **Two switches per newsletter, stored in the `settings` table:** `desk_editions` (offered on the
  Broadcast Desk) and `auto_editions` (built overnight). Kept separate so an edition can sit one
  click away without joining every night's run — which matters when editions synthesize one at a
  time and the plexbox takes 8–15 min each. **Auto implies Desk**, enforced on every read so the
  invariant holds however the rows were written, and mirrored in the UI (ticking Auto ticks Desk;
  clearing Desk clears Auto).
- **`AUTO_BROADCAST_EDITIONS` is now a seed, not the source.** It supplies the first read on a
  fresh install so a new box still comes up configured from `.env`; after that the table wins and
  changing the line-up needs no redeploy. The Desk defaults separately to the three editions it
  always shipped with, so an env var that narrows the night run can't also empty the Desk.
- **New `GET /api/editions`** returns the catalog with both flags plus a per-edition mean episode
  length, so one payload drives the Desk's station buttons and the Settings panel and they can't
  drift. The Settings panel shows the audio your Auto selection adds up to per night, measured from
  your own episodes (an edition that has never run borrows the average; with nothing to go on the
  estimate is omitted rather than invented).
- **`POST /api/jobs` now validates slugs against the catalog** and reports them in a `rejected`
  list. An unknown slug used to queue a job that fetched a URL tldr.tech will never serve and then
  reported it as "not published".
- **Design system: 11 new station colours**, placed by max-min hue spacing on the OKLCH circle,
  anchored on the three approved editions (unchanged) and skipping the band around signal-orange.
  Every edition lands between **4.99:1 and 7.29:1** on its panel in both themes — the same band the
  original three already occupied. Colour is now driven by a `--st` custom property set inline from
  the catalog, so a 15th edition needs two tokens and no new CSS rules (the alternative was ~42
  near-identical declarations).
- **`/api/parser/health` now spaces its fetches.** tldr.tech throttles bursts, and a throttled
  response is indistinguishable from "not published" — see `lessons_learned.md`. Broadcasts were
  never at risk (the worker runs one job at a time), but this route is a dozen-plus fetches back to
  back. Verified: 5 editions resolve correctly in 7.2 s.
- **QA covers the new surface**: waits for the async-rendered stations, counts the Desk/Auto
  checkbox pairs, drives the auto-implies-Desk interaction in both directions, and measures the
  new panel's contrast (`.st-name` 15.79:1, `.st-desc` 8.77:1, estimate 8.77:1).
- 64 tests (was 46), ruff clean, full `qa/qa.py` with **zero issues** and `contrast_below_aa`
  unchanged from the pre-existing baseline.

## [v0.8.1] - 2026-08-07
**The plexbox's silent voice audition — found, reproduced, fixed.** Open since v0.7.2 and
un-reproduced across three deploys. Prod had recommended closing it as unreproducible; the lead
that cracked it came back from the box: *every* attempt had been Chromium.
- **Root cause needed two variables at once**, which is why isolating either kept coming up clean:
  **WebKit** abandons a media load that stalls past **~16 s**, setting `MediaError.code 4`
  (`MEDIA_ERR_SRC_NOT_SUPPORTED`) while still reporting `paused: false` — silence, not an error —
  where **Chromium waits indefinitely**; combined with a **slow first synth**, since
  `/api/voices/audition/{voice}` synthesizes inside the request (1.27 s on the dev Mac, ~10–20×
  that on prod, landing right on the threshold). Measured: WebKit played at 15 s, silent
  at 18 s; Chromium fine at 20 s+.
- **Fix — split the gesture from the wait.** On click, synchronously play 8 ms of silent WAV from a
  `data:` URI (no network, so it cannot stall). That **unlocks** the element — one that has played
  from a user gesture accepts a later programmatic `src` + `play()` without a fresh one — after
  which the clip is `fetch`ed at leisure and handed over as a `blob:` URL it can decode instantly.
  The media loader never waits on the server. Verified playing through a **45 s** stall, 3× the old
  limit. v0.7.2's insight (playback must start inside the click) is preserved; what changed is that
  the element no longer sits behind the synthesis.
- **The failure path got better too.** `reportAuditionFailure` used to re-probe the URL through the
  same stall, so a tooltip took another ~20 s; the fetch now *is* the probe and reports
  `HTTP 500 Internal Server Error` or `server sent text/html` immediately. A 120 s abort turns a
  true hang into a message instead of an indefinite "Preparing…".
- **New QA stage `audition_stall_qa`** — WebKit with a 20 s stall injected at the network layer
  (deterministic, ~20 s, no need to actually slow Kokoro). **Verified by watching it fail:**
  reverting `app.js` makes it report `err: 4` and "the media element is back on the far side of the
  synth".
- **Caught while building that guard:** its first version passed on the *unlock clip* —
  `!paused && currentTime > 0` is satisfied by 8 ms of silence, so it went green against code that
  was still broken. Both audition stages now require `audio.dataset.voice` to match and the source
  to be a `blob:` URL.
- 64 tests, ruff clean, full `qa/qa.py` (4 stages) **zero issues**, `contrast_below_aa` unchanged
  at 15.
