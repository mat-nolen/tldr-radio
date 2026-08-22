# Version History

## [v0.10.3] - 2026-08-22
- **Security: the container no longer runs as root.** The `Dockerfile` had no `USER` directive, so
  uvicorn ran as root inside the container (CWE-250). Reported on the public repo by an automated
  scanner the day after the project was featured (`#2` / `#3`). The image now creates `appuser`
  (uid/gid 1000) and switches to it; nothing in the app needs root, and every runtime write goes to
  `/data`.
- **The directive alone would have broken production.** `/data` is a host bind mount, so once the
  app stops being root the *host* directory's owner decides whether anything can be written. The
  reported patch (`USER 1000`, no account created, no ownership handling) would have passed on
  macOS — Docker Desktop remaps mount permissions — and failed on the Linux box, which is the same
  dev/prod asymmetry as the v0.10.1 compose variable.
- **So a permission mismatch is now loud instead of silent.** `ensure_data_dir_writable` runs
  before anything else in the app's lifespan and refuses to start with a message that names the
  uid, the reason and both fixes. Without it the first symptom would have been a broadcast dying
  part-way through, hours later, with a stack trace about sqlite.
- **`APP_UID` / `APP_GID` in compose** let a host whose `./data` belongs to someone else fix it in
  `.env` instead of rebuilding the image. Documented in `.env.example`.
- **152 → 159 tests.** The three static guards were verified by reverting to the old Dockerfile,
  to the reported patch, and to a pinned compose `user:` — each fails the guard it is aimed at.
  Verified **in Docker** both ways: a writable mount starts, serves `/api/health` 200 and writes
  `episodes.db` as uid 1000; a read-only mount refuses to start with the actionable error.
- ⚠️ **Upgrading an existing install needs one check.** On a Linux host, confirm `./data` is owned by uid 1000, or set `APP_UID`/`APP_GID` in `.env` to whoever owns it. Missing it is not silent: the app refuses to start and prints the fix.

## [v0.10.2] - 2026-08-18
- **Fix: one dropped Kokoro connection no longer destroys a whole episode.** `KokoroClient.synthesize`
  now retries transient failures (any `httpx.TransportError` — dropped connection, timeout, protocol
  error — plus 429 and 5xx) up to **4 attempts** with 1s/2s/4s backoff. A 4xx is never retried: the
  request is wrong and will be wrong again. Found in the wild 2026-08-17 when the `ai` edition died on
  `RemoteProtocolError: peer closed connection without sending complete message body` **after ~20 of
  its chapters were already on disk**; prod builds `ai` nightly and it is the largest edition.
- **Fix: a permanently failed chapter now cancels its siblings.** `synthesize_chapters` used
  `asyncio.gather`, which stops waiting on a failure but leaves the other chapters running — burning
  CPU on a dead episode and writing mp3s into a directory the retry is about to reuse. Now an
  `asyncio.TaskGroup`, which cancels them, with the underlying exception unwrapped from the
  `ExceptionGroup` so the job still records `RemoteProtocolError: …` rather than "unhandled errors
  in a TaskGroup" — prod reads that string.
- The mp3 is written only after a complete read, so a dropped connection cannot leave a truncated
  file for the concatenator to trip over.
- **144 → 152 tests.** The seven that matter were verified by reverting the fix and watching them
  fail; the eighth is a regression guard on the unwrapping. Verified end to end **in Docker** against
  real Kokoro: `tech 2026-08-04`, 15 stories, 17 chapters, 345 s of audio, none truncated, no errors.
- Tooling (not shipped in the image): `scripts/demo_{capture,cards,build}.py` generate the demo video,
  `scripts/demo_stage.sh` stages a window for a hand-driven screen recording. See `docs/demo-video.md`.

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

## [v0.9.0] - 2026-08-12
- **Sponsor reads are now a build-time option, off by default on this box and on for a fresh
  install.** The motivation is not a feature request: publishing TLDR's editorial with the ads
  stripped is the sharpest version of the copyright problem. A sponsor read is part of the issue,
  and an audio edition that silently removes it is not a faithful one.
- **`kind='sponsor'` — a fourth chapter type**, emitted where the newsletter puts it, not appended.
  A sponsor is a real, skippable chapter: dashed rail, `◆` mark and a `SPONSOR` pill in the list so
  you see one coming, and a now-playing card whose eyebrow reads *Sponsored* in place of the
  section and `SPONSOR` in place of `STORY n / m`. The parallel is the printed issue, where your
  eye slides past the ad block.
- **Sponsors are never stories.** They take no section glue, no story number, and no place in the
  intro line or `episodes.story_count`. `idx` had been doing double duty as both chapter position
  and story number — that only worked while chapters and stories were 1:1, so the frontend now
  derives the story number by counting `kind === 'story'`.
- **A different voice, plus a spoken disclosure.** `SPONSOR_VOICE` defaults to `bm_george` —
  British male against the American-female default, distinct on accent *and* register so the switch
  lands even at 2×. Because the voice is a setting a listener can change, the disclosure is also
  carried in the words (`"A word from our sponsor."`). `synthesize_chapters` resolves voice per
  chapter; an unset `sponsor_voice` means "same as the main voice", not some other default.
- **Build-time, deliberately.** Prod is CPU-bound, so synthesizing ads nobody wants is pure waste.
  The consequence is that toggling only affects the **next** broadcast — episodes already on disk
  keep the shape they were built with. That sentence is in the Settings panel, and QA asserts it
  survives, because it is the one thing about this setting that will otherwise surprise you.
- **No schema migration.** `settings` is key/value and `chapters.kind` is TEXT, so the prod DB needs
  nothing. Settings follow the established pattern: env seeds the first read, the table wins after.
- **🐛 Caught in my own change:** putting the pill inside `<h4>` made `h4.textContent` read
  `"SponsorHeadline…"`, which would have silently broken QA's card-vs-list comparison — the guard
  added in v0.7.3. The headline now has its own `.ch-head` span.
- **Verified against live tldr.tech, not fixtures.** `2026-08-10` built with the toggle on → 2
  sponsor chapters, `story_count=13`; `2026-08-07` built with it off → 0 sponsors, `story_count=14`;
  and the Aug 10 episode still has its ads after the toggle went off. Sponsor voice confirmed by
  duration (stored clip 43.824 s == `bm_george`, ≠ `af_heart` 41.016 s) — **Kokoro is not
  byte-deterministic**, so the hash probe tried first was inconclusive.
- 83 tests (was 64), ruff clean, full `qa/qa.py` (4 stages) **zero issues**. `contrast_below_aa`
  measured **17 → 17** against a re-measured baseline; the 15 in older notes predates the episodes
  now in the library. Two entries are new (`.sp-voice .eyebrow`, at the same 3.5/4.22 as the
  `.voice-id` and `.rule-label` beside it); three fell off only because QA now leaves a sponsor
  chapter selected, changing the measured background — not an engineered improvement.

## [v0.9.1] - 2026-08-12
- **Phones had no navigation at all.** The masthead hid every nav link below 640px *except*
  `[aria-current="page"]` — a link to the page you are already on — so the one link a phone kept
  was the only useless one. Opening an episode stranded you on the player. Every iPhone width is
  below that breakpoint; iPad portrait at 768px is above it, which is why the tablet looked fine.
  Links now all stay, and the space comes out of decoration instead: the wordmark drops "RADIO",
  the theme button drops its text label (keeping its icon and `aria-label`), and under 360px the
  wordmark reduces to the signal dot.
- Nav links get a **44px touch target at every width**, not just on phones — an iPad is a touch
  device too and the 68px masthead absorbs it.
- **Console title** no longer breaks "TLDR Tech — 2026-08-10" across three lines by splitting the
  date at its own hyphen; smaller type on mobile plus a nowrap span on the date.
- **Transport** was a wrapping flex row, so Next fell to a second line while Previous stayed on the
  first — the two most-used controls, separated. Fixed two-row grid now.
- **New `mobile_qa` stage** drives WebKit at four real device sizes and asserts an *off-page* link
  is reachable, nothing scrolls sideways, tap targets clear 44px, and the transport stays one row
  (comparing vertical centres — the play button is deliberately larger). Both guards were verified
  by watching them fail against the old CSS: 21 issues, and a 62px transport spread. Desktop-only
  QA cannot see a media query it never triggers.

## [v0.10.0] - 2026-08-13
- **Download a whole episode as one tagged mp3** — `GET /api/episodes/{id}/download`, plus a button
  on the player. Until now audio could only be copied off the box by hand, one file per story.
- **No `ffmpeg`, and that was the gating question.** mp3s join by plain byte-append; verified
  against three independent decoders — `mutagen` and CoreAudio (`afinfo`, the parser iOS audio apps
  use) both at 387.360 s, and WebKit seeking exactly to 10 s / 200 s / 380 s in the result.
- **It works because of two properties, and both are now asserted on every build.** Kokoro emits
  identical CBR frames with **no Xing/Info header**: a Xing header would make every decoder report
  *chapter 0's* duration for the whole file, and VBR would seek wrongly while still playing.
  `assert_concatenable` fails loudly and names the file if either changes.
- Each chapter carries a 45-byte ID3v2 tag; appended as-is, 16 of them are counted as audio and
  drifted a 6.5-minute episode by 45 ms. Every tag but the first is stripped.
- **Built on demand, cached beside the chapters.** The backlog assumed disk-vs-CPU, but joining is
  a byte copy rather than a re-encode — so only episodes actually downloaded cost disk, and
  retention/delete already `rmtree` that directory. Written to a temp name and renamed, so a crash
  mid-write can't leave a truncated file that later looks like a valid cache hit.
- **Tagged for an audiobook shelf**, not a podcast feed: album groups an edition, title identifies
  the episode, `TPE1` fills the author field most audiobook apps display. Without these it lands as
  a bare filename.
- **🐛 Found by the new icon guard, pre-existing:** `base.css` gives `svg { max-width: 100% }`, so
  an icon inside a stretched flex button shrinks to **zero width** while still reporting visible.
  It hid the new Download icon — and the Settings audition icons had been doing the same at 375px,
  unnoticed, since the voice picker was built.
- 109 tests (was 64 at v0.8.1), ruff clean, QA five stages zero issues — **run against the Docker
  stack, not just bare metal**, since the combined file is written into the mounted `/data` volume.

## [v0.10.1] - 2026-08-13
- **🐛 Fixed: `.env` settings that silently did nothing in Docker.** `docker-compose.yml` never
  forwarded `INCLUDE_SPONSORS` or `SPONSOR_VOICE` to the app container, so the v0.10.0 deploy
  instruction ("set `INCLUDE_SPONSORS=false` to keep prod ad-free") had no effect — the value fell
  through to the code default and the box came up serving sponsor reads. **Found on the plexbox,
  not here**: nothing errors, the setting is simply ignored, and only a real deploy following the
  written instruction exposes it.
- Same fix applied to **`DEFAULT_VOICE` and `RETENTION_DAYS`**, which were pinned to literals
  (`af_heart`, `"14"`) and equally un-overridable. All four are now `${VAR:-default}` passthroughs
  with identical defaults, so nothing changes unless someone sets them.
- All four documented in `.env.example`, which had never mentioned them.
- **`tests/test_compose_env.py` guards the whole class**: every variable `config.py` reads must
  appear in compose's app `environment:`, be a `${...}` passthrough rather than a pinned literal,
  and be documented in `.env.example` — with a short allowlist for the three the container fixes
  on purpose (`DATA_DIR`, `KOKORO_URL`, `APP_PORT`). Verified by reverting compose to the shipped
  state and watching 6 tests fail. `os.environ.get` in Python and a line in compose are two halves
  of one contract, and nothing was checking it.
- Verified inside the container, not just by reading the file: with `INCLUDE_SPONSORS=false`,
  `config.include_sponsors` is `False` in the running image.
- 144 tests (was 109), ruff clean.
