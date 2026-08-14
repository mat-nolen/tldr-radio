# Lessons Learned

> Log issues immediately when encountered. Always include a Topic tag.
> Topics: Python | GUI | Packaging | pytest | Web | CLI | Async | Git | Other
>
> Global lessons learned (Python): ~/.claude/global/python/
> Global lessons learned (Shared): ~/.claude/global/shared/

## Issue: hwdsl2/kokoro-server arm64 image crashes on Apple Silicon (CUDA torch)
- **Date:** 2026-07-23
- **Topic:** Packaging
- **Problem:** During Spike #0, `hwdsl2/kokoro-server:latest` (arm64, build 2026-07-16) exits(1) at
  startup: `import torch` → `_preload_cuda_deps` → `ValueError: libcublasLt.so.*[0-9] not found`.
  The arm64 image ships a **CUDA build of torch 2.13.0** (`libtorch_cuda.so`, `libc10_cuda.so`,
  `libcaffe2_nvrtc.so` in `torch/lib/`), which hard-crashes on any host without an NVIDIA GPU —
  i.e. every Apple Silicon Mac. Confirmed `:latest-arm64` is the same digest (`a6b272f4…`), so it's
  not a tag mix-up; the arm64 build itself is broken. No env flag fixes it.
- **Solution:** Switched the TTS engine to **Kokoro-FastAPI CPU** (`ghcr.io/remsky/kokoro-fastapi-cpu`)
  — same Kokoro-82M, same OpenAI-compatible `/v1/audio/speech` + `/v1/audio/voices` on `:8880`, with a
  native linux/arm64 build. Zero-host-dependency Docker design preserved; only the image name changes.
- **Prevention:** Spike #0 (run + time the real image before building) is exactly what caught this.
  Always execute the TTS container on the target arch before designing around it — don't trust
  "multi-arch" labels. Also noted: hwdsl2's persistent volume auto-generates a Bearer token; for a
  local $0/no-auth app set `KOKORO_API_KEY=` empty.

## Issue: "not published" looked identical to "the parser broke"
- **Date:** 2026-07-25
- **Topic:** Web
- **Problem:** On a day with no issue (weekend/holiday/not posted yet) `tldr.tech/<edition>/<date>`
  does **not** 404 — so `NotPublishedError` never fired. It answers **200 and redirects** to
  `/<edition>` (or all the way to `/`) and serves the landing page. With `follow_redirects=True` the
  client happily returned that page, cached it under the *issue's* cache key, and the parser raised
  "Parser found 0 non-sponsor stories — layout may have changed": 3–6 loud failures every weekend,
  and the cached junk then masked every later fetch for that date. Confirmed live for all three
  editions on Sat 2026-07-25 (tech → `https://tldr.tech/`, ai/infosec → `/<edition>`), and for
  pre-archive dates like 2019-01-01.
- **Solution:** Compare the response's **final URL** with `/<edition>/<date>` — a redirect away means
  "no issue", raise `NotPublishedError` and **never cache** the body. Kept the loud `EmptyParseError`
  for a page that *is* the dated issue but parses to nothing (the real layout alarm). Added a
  positive issue-page marker for self-healing existing junk caches: every real issue renders its date
  in the H1 (`TLDR AI 2026-07-24`, verified across 15 pages), so a cached page with neither a dated
  H1 nor a story `<article>` is discarded and re-fetched. Jobs got a `skipped` terminal state so a
  quiet outcome stops looking like a failure.
- **Prevention:** Never infer "resource missing" from a status code alone when the server is a
  marketing site — a 200 can be a redirect to something else entirely. Check the **final URL** after
  redirects, and cache only what you positively identified. When adding a "quiet skip", give it its
  own terminal state instead of reusing the failure path, or the noise just moves.

## Issue: retrying a job collided with the row its first attempt persisted
- **Date:** 2026-07-25
- **Topic:** Python
- **Problem:** `episodes` has `UNIQUE (edition, issue_date)` and the pipeline persists the episode +
  chapter rows *before* synthesis (so the library renders immediately). A job that died during
  synthesis therefore left a row behind, and re-running it hit
  `sqlite3.IntegrityError: UNIQUE constraint failed: episodes.edition, episodes.issue_date` — which
  then became the job's reported error, **masking the original failure**. Latent since Phase 4 and
  reachable from the manual Retry button; it only became obvious when the overnight broadcast started
  retrying failures automatically.
- **Solution:** `_process` now checks for an existing episode first: if it's already `ready` it adopts
  the row and returns (a stale card's Retry must never rebuild a finished episode), and if it's
  partial it deletes the row + its audio dir before inserting. Reproduced with a test *before*
  fixing (`tests/test_worker_retry.py`), then verified live by stopping the Kokoro container
  mid-broadcast and retrying → one clean `ready` episode.
- **Prevention:** Any "insert before the slow work" design needs an explicit re-entry path — write the
  retry test at the same time as the retry button, not when you later automate it. And when a retry
  reports an error, be suspicious that it's the *retry's* error, not the original one.

## Issue: swallowed promise rejection made a real bug invisible
- **Date:** 2026-07-25
- **Topic:** Web
- **Problem:** The Settings audition handler was `try { await fetch(...); await audio.play(); }
  finally { ... }` — a `finally` with **no `catch`**. On the prod box, clicking Audition on an
  uncached voice played nothing and reported nothing, so there was no error to debug at all.
- **Solution:** Added a `catch` that surfaces the reason (console + a `title` on the button), and made
  it informative by probing the URL and reporting the HTTP status when the media error itself is
  useless ("no supported source was found"). Also switched to a `GET` route as `audio.src` so the
  element owns the slow first synth instead of an awaited `fetch` + blob.
- **Prevention:** `try/finally` around an `await` is a silencer — if there's no `catch`, the rejection
  vanishes. Any `await`ed browser API whose failure the user must see needs an explicit `catch` that
  *shows* something. Also: don't trust a stated cause without reproducing it — the reported
  "user-activation window expired" explanation was wrong (Chrome gates media on **sticky**
  activation, which never expires; the old pattern still played after a forced 6 s delay), so the
  surfaced message, not the rewrite, is what will identify the real prod failure.

## Issue: Playwright QA surfaced 3 frontend bugs
- **Date:** 2026-07-24
- **Topic:** Web
- **Problem:** (1) `el.toggleAttribute('data-current', bool)` sets the attribute to an *empty* value,
  so the CSS selector `[data-current="true"]` never matched — the current chapter/episode highlight
  silently didn't render. (2) The `hidden` attribute did nothing on `.on-air` because the class rule
  `display:inline-flex` overrides the UA `[hidden]{display:none}` (equal specificity, later author
  rule wins) — "ON AIR" showed permanently. (3) `page.goto(..., wait_until="networkidle")` hangs
  forever on pages with an open `EventSource` (SSE never goes idle).
- **Solution:** (1) Use `setAttribute('data-current','true')` / `removeAttribute` instead of
  `toggleAttribute`. (2) Add a global `[hidden]{display:none!important}` to base.css. (3) Use
  `wait_until="load"` + explicit `wait_for_selector`. Also fixed a title-case bug ("TLDR Ai" → map
  to "TLDR AI") and a stale-render flash (clear title/chapters before the episode fetch resolves).
- **Prevention:** Prefer `setAttribute(name, value)` when a CSS selector checks the value; ship the
  `[hidden]` reset in every design system; never use `networkidle` on SSE/websocket pages. The
  Playwright pass (`qa/qa.py`) that clicks every episode + sweeps the audio API catches all of these.

## Issue: a themed ink scale on a surface that does not theme
- **Date:** 2026-08-01
- **Topic:** Web
- **Problem:** The player's chapter list was **unreadable in Daytime theme** — headlines at
  `--ink` (#1C1712) on the console faceplate `--console` (#241E17), a contrast ratio of ~**1.05:1**.
  The faceplate is deliberately dark in *both* themes, but `--ink`/`--ink-2`/`--ink-3` invert with
  the theme, so the pairing only works at night. It shipped through a full Playwright QA pass, three
  phases and a deploy because **every screenshot was taken in the dark theme** — the QA toggles the
  theme and asserts the attribute changed, but captures its images before/independently of that.
- **Solution:** `base.css` now gives `.console` its own ink scale (`h1–h4` → `--console-ink`;
  `.muted`, `.rule-label`, `.mono`, `.eyebrow` → `--console-ink-2`), so anything placed on the
  faceplate is correct by default. The current-chapter row mixes its highlight against the faceplate
  — `color-mix(in srgb, var(--signal) 16%, var(--console-2))` — rather than using `--signal-tint`,
  which flips to a pale wash in Daytime and would erase the light text sitting on it.
- **Prevention:** When a design system has a surface that does *not* follow the theme (a faceplate,
  an always-dark header), give that surface its own token scale and scope it — never let it inherit
  the themed one. `qa/qa.py` now captures every page in **both** themes *and* measures real contrast
  (composited ancestor background vs rendered colour), because a screenshot only helps if somebody
  looks at it — an assertion that only checks `data-theme` flipped can't see any of this. Threshold
  chosen deliberately: <3:1 fails the run, 3–4.5:1 is recorded as `contrast_below_aa` so the approved
  palette's muted metadata stays visible without turning QA into noise. It paid for itself on the
  first run: the new collapsible day header had been styled `--ink-3` as if still a passive label
  (3.0:1) when collapsing had made it the only way into a day — now `--ink-2`, 8.01:1.

## Issue: a padding shorthand silently deleting another class's padding
- **Date:** 2026-08-01
- **Topic:** Web
- **Problem:** Every page rendered flush to the viewport edge on a phone and scrolled sideways.
  `.wrap` sets `padding: 0 var(--space-6)` for the page gutters and `.page` sets
  `padding: var(--space-8) 0 var(--space-9)` for vertical rhythm. They are always used together
  (`<main class="wrap page">`), same specificity, and `.page` is defined later — so its **shorthand
  reset the inline padding to 0**. Nothing errors; the gutters just vanish. Invisible on desktop,
  where `max-width` + `margin: auto` supply the whitespace anyway.
- **Solution:** `.page { padding-block: … }` — the logical longhand touches only the axis it means
  to. Separately, the chapter row's third column (clip length + "read more", both `white-space:
  nowrap`) could not fit beside a headline at 390px and forced the layout wider than the viewport;
  below 640px it now drops onto its own line.
- **Prevention:** In a composable-utility CSS system, **never use a shorthand in a class meant to be
  combined** — reach for `padding-block`/`padding-inline`/`margin-block`. And assert it: comparing
  `documentElement.scrollWidth` to `clientWidth` across breakpoints catches every horizontal
  overflow in one cheap check, which no screenshot review reliably does.

## Issue: one dead QA stage threw away every other result
- **Date:** 2026-08-01
- **Topic:** pytest
- **Problem:** `qa/qa.py` ran `audio_sweep(); browser_qa(); audition_qa()` at module level and
  printed the report last. With Kokoro down, `audition_qa`'s `/api/voices` call returned an error
  body, `.json()` raised an uncaught `JSONDecodeError`, and the process died **before**
  `print(json.dumps(R))` — so a dependency being unavailable destroyed the results of the two
  stages that had already passed, including every check unrelated to Kokoro.
- **Solution:** Stages run in a loop that catches per stage and records the crash as an issue, so
  the report always prints. `audition_qa` additionally checks the response explicitly and degrades
  with an actionable note ("is Kokoro up?") rather than raising.
- **Prevention:** A diagnostic tool's own output is the one thing that must not be lost to a
  failure — it is what you read to *understand* the failure. Collect results incrementally, isolate
  each stage, and emit the report from a path that cannot be skipped. The same reasoning as the
  `catch`-less audition bug in v0.7.2: the failure wasn't the problem, the silence was.

## Issue: tldr.tech throttles burst fetches, and a throttled reply looks exactly like "not published"
- **Date:** 2026-08-06
- **Topic:** Async
- **Problem:** While surveying which of the 14 newsletters exist, a probe fired all 14 archive
  fetches concurrently. Several came back **404** — including `tech` for a date it had definitely
  published. Re-fetching the same URL serially returned 200 with a full issue. A second sweep at
  concurrency 3 was still wrong, reporting `tech` as publishing 5 days in 14 when a serial run
  proved it publishes every weekday, 10/10. The failure is silent and *plausible*: the app's own
  `NotPublishedError` covers exactly a 404 or a redirect to the landing page, so throttled requests
  are indistinguishable from a genuine weekend — they'd have produced amber "not published" cards
  nobody would question.
- **Solution:** Space out any loop that fetches several editions with no real work in between.
  `/api/parser/health` now sleeps `PARSER_HEALTH_DELAY_SECONDS` (1.5 s) between editions; 5
  editions resolve correctly in 7.2 s. Broadcasts were never affected — the worker is a
  single-consumer loop with minutes of synthesis between fetches — and that property is now a
  constraint worth preserving, not an accident.
- **Prevention:** When "resource absent" and "you're being rate-limited" produce the same response,
  concurrency turns a load problem into a **correctness** problem, and the wrong answer looks
  ordinary. Before trusting any measurement taken in parallel against a third-party host, re-run
  one case serially and compare. Verify a survey's *method* before believing its conclusions —
  the first sweep here would have shipped a table of publishing schedules that was simply false.

## Issue: WebKit abandons a stalled media load; Chromium waits — a silent bug no Chromium test could find
- **Date:** 2026-08-07
- **Topic:** Web
- **Problem:** Voice auditions failed **silently** on the prod box and had survived three deploys
  un-reproduced. The repro needs **two** variables at once, which is why isolating either one kept
  coming up clean:
  1. **WebKit, not Chromium.** WebKit abandons a media load that stalls past **~16 s** and sets
     `MediaError.code 4` (`MEDIA_ERR_SRC_NOT_SUPPORTED`) — while still reporting `paused: false`
     with `currentTime` frozen at 0, so it reads as pure silence rather than an error. Chromium
     waits indefinitely. Playwright WebKit played at 15 s and went silent at 18 s; Chromium played
     at 20 s+.
  2. **A slow first synth.** `/api/voices/audition/{voice}` synthesizes on demand *inside the
     request*. That is 1.27 s on the dev Mac but ~10–20× that on prod — landing right on
     WebKit's threshold, which is also why it was intermittent there and never seen on the Mac.
  The v0.7.2 fix was correct as far as it went — playback must start inside the click or the
  autoplay policy rejects it — but starting playback inside the click put the `<audio>` element on
  the far side of a multi-second synthesis.
- **Solution:** Split the gesture from the wait. On click, synchronously play 8 ms of silent WAV
  from a `data:` URI — no network, so it can never stall — which **unlocks** the element; a media
  element that has played once from a user gesture accepts a later programmatic `src` + `play()`
  without a fresh one. Then `fetch()` the clip at leisure, hand the element a `blob:` URL it can
  decode immediately, and play. The media loader now never waits on the server: verified playing
  through a **45 s** stall, 3× the old limit. As a bonus the failure path got faster and more
  specific — the old `reportAuditionFailure` re-probed the URL through the same stall (another
  20 s before the tooltip appeared); the fetch now *is* the probe, so it reports `HTTP 500` or
  `server sent text/html` immediately.
- **Prevention:** Two lessons, and the second is the sharper one.
  - **A cross-browser bug cannot be found by one engine, and "it's all Chromium" is easy not to
    notice** — Playwright's default, Chrome, and the box's automation were all Blink. `qa/qa.py`
    now has an `audition_stall_qa` stage running **WebKit with a 20 s stall injected at the network
    layer** (deterministic, ~20 s, no need to actually slow Kokoro).
  - **A regression guard must be watched failing before it is trusted.** The first version of that
    stage passed on the *unlock clip*: `!paused && currentTime > 0` is satisfied by 8 ms of
    silence, so it went green against code that was still broken. Both audition stages now require
    `audio.dataset.voice` to match and the source to be a `blob:` URL. Confirmed by reverting
    `app.js` and watching the stage fail with `err: 4` before keeping it.

---

## Issue: a TTS engine that is not byte-deterministic, and the verification that assumed it was
- **Topic:** Testing · TTS · Verification
- **Date:** 2026-08-12
- **Symptom:** Needed to prove that sponsor chapters were synthesized in the *sponsor* voice and not
  the main one. Re-synthesized the same text with both voices and compared SHA-256 against the
  stored chapter mp3. **Neither matched.** Read naively that is "the feature is broken twice over".
- **Cause:** Kokoro does not produce identical bytes for identical input. Synthesizing the *same
  text in the same voice twice* gives two different files. The probe was measuring engine
  nondeterminism, not voice identity, so it could never have returned a match.
- **Solution:** Compare **duration** instead — a stable, voice-dependent property. The stored
  sponsor clip was 43.824 s, exactly matching a fresh `bm_george` render and clearly apart from
  `af_heart` at 41.016 s. Backed by a unit test using a recording fake client that captures the
  voice argument per chapter, keyed on `script_text` rather than call order (chapters go through a
  semaphore and `gather()`, so arrival order is not a guarantee worth asserting on).
- **Prevention:** Before using equality of an artifact as evidence, **prove the artifact is
  deterministic** — one control run of the same input twice would have shown this immediately, and
  it cost a round of chasing a bug that did not exist. Where the generator is nondeterministic,
  assert on a derived invariant (duration, length, a parsed field) or instrument the call itself.

---

## Issue: a decorative label inside a heading silently broke an existing QA guard
- **Topic:** QA · Frontend
- **Date:** 2026-08-12
- **Symptom:** Adding a `SPONSOR` pill to the chapter row — `<h4>{pill}{headline}</h4>` — meant
  `h4.textContent` returned `"SponsorRoll out AI-generated code…"`. The v0.7.3 QA check that the
  now-playing card tracks the highlighted chapter compares that text to `#nc-title`, so it would
  have started reporting a mismatch on every sponsor chapter: a *false* failure, in a guard whose
  whole job is catching a *real* drift between the two.
- **Cause:** `textContent` is the concatenation of every descendant. A heading that also carries a
  badge is no longer a reliable source of "the heading's text".
- **Solution:** Give the headline its own element — `<h4>{pill}<span class="ch-head">{headline}</span></h4>`
  — and point the QA selector at `.ch-head`.
- **Prevention:** When adding a child to an element something else reads text from, **check who
  reads it**. Grepping the selector (`.chapter h4`) across `qa/` found this before the run did. The
  broader habit: a decorative element inside a semantic one should be a sibling of a dedicated text
  node, not a bare prepend.

---

## Issue: mp3s can be concatenated by byte-append — but only because of two properties worth checking
- **Topic:** Audio · TTS · Dependencies
- **Date:** 2026-08-13
- **Symptom / question:** Downloading an episode needs one file, not 17 chapters. The assumption
  worth testing was whether `ffmpeg` had to enter the Docker image to join them.
- **Finding:** It does not. Kokoro emits identical frames per chapter (24 kHz mono, MPEG-2
  Layer III, 128 kbps CBR) and a plain byte-append produces a valid file — confirmed by three
  independent decoders: `mutagen` 387.360 s, CoreAudio (`afinfo`, the parser iOS/macOS audio apps
  use) 387.360 s, and WebKit at 387.36 s seeking exactly to 10 s / 200 s / 380 s.
- **The two properties it depends on**, both now asserted on every build in `assert_concatenable`:
  1. **No Xing/Info header.** That header carries a frame count for the whole file. With one
     present, every decoder reads *chapter 0's* duration and reports it for the join — the classic
     "concatenated mp3 shows the wrong length and seeks into nowhere". Kokoro happens not to write
     one; an upgrade that started to would break this silently.
  2. **Constant bitrate.** Without a Xing header a decoder derives position from byte offset, which
     is only accurate for CBR. VBR would still play but seek wrongly — the worse failure, because
     it looks fine until you scrub.
- **Detail that is easy to miss:** each chapter carries a 45-byte ID3v2 tag. Byte-appending
  embeds one mid-stream per chapter, where a decoder counts it as audio. Left in, 16 of them
  drifted a 6.5-minute episode by 45 ms — trivial once, but it means the reported length is never
  quite right and it scales with chapter count. Strip every tag but the first.
- **Prevention:** When a format trick works, write down *why* it works and assert it, rather than
  recording that it worked. The guard costs one `MP3()` read per chapter and turns a future silent
  breakage into a loud, specific error naming the file and the reason.

---

## Issue: an icon inside a stretched flex button collapses to zero width and vanishes
- **Topic:** Frontend · Responsive · QA
- **Date:** 2026-08-13
- **Symptom:** The new Download button rendered as text with no icon on a phone, while looking
  right on desktop. The `<svg>` reported `visibility: visible` and a height of 15px — but a width
  of **0**.
- **Cause:** `base.css` sets `img, svg, canvas { max-width: 100% }`. Inside a flex button that has
  been stretched (`flex: 1`), the svg is itself a flex item with the default `flex-shrink: 1`, so
  it is free to shrink — and does, all the way to nothing. Nothing reports an error; the element
  is simply zero-width.
- **Solution:** `flex: none` on the icon.
- **Prevention:** A general QA check now walks every visible `button svg` / `a.btn svg` at phone
  widths and flags any with height but no width. It immediately found a **pre-existing** instance
  of the same bug — the Settings page's Audition icons had been collapsing at 375px, unnoticed,
  since the voice picker was built. A guard written for a new bug is worth pointing at the old code
  too; this one paid for itself on its first run.

---

## Issue: a setting added in Python but never forwarded by Compose — documented, and silently ignored
- **Topic:** Docker · Config · Deploy
- **Date:** 2026-08-13
- **Symptom:** v0.9.0 added `INCLUDE_SPONSORS`, and the deploy notes told prod to set
  `INCLUDE_SPONSORS=false` in the box's `.env` to keep it ad-free. The box came up with sponsor
  reads **on**. Nothing errored, nothing logged, and the app was behaving perfectly correctly —
  it had simply never been handed the variable.
- **Cause:** `docker-compose.yml`'s app service has an explicit `environment:` block and no
  `env_file:`. A Compose service sees **only what it is handed**, so a variable absent from that
  block does not exist inside the container no matter what `.env` says. `config.py` fell through
  to its code default (`True`).
- **Why local testing could never catch it:** bare-metal dev reads the developer's own shell
  environment, so the variable resolves. The gap only exists in the container, and only for
  someone following the documented instruction — which is to say, only in production.
- **Not one variable, but a class.** Auditing every name `config.py` reads against compose found
  `SPONSOR_VOICE` missing too (added in the same release, unreported), plus `DEFAULT_VOICE` and
  `RETENTION_DAYS` pinned to literals — `KEY: "value"` rather than `KEY: "${KEY:-value}"` — and
  therefore equally impossible to override.
- **Solution:** forward all four as `${VAR:-default}`, document them in `.env.example`, and add
  `tests/test_compose_env.py`: every variable `config.py` reads must be present in compose, be a
  passthrough rather than a literal, and appear in `.env.example`, with a short allowlist for the
  three the container fixes deliberately (`DATA_DIR`, `KOKORO_URL`, `APP_PORT`). Verified by
  reverting compose to the shipped state and watching six tests fail.
- **Prevention:** `os.environ.get` in code and a line in `docker-compose.yml` are **two halves of
  one contract**, and adding only the first looks complete while being half-done. Where two files
  must agree and nothing enforces it, write the test that enforces it — the guard here is
  mechanical, runs in milliseconds, and would have caught the bug the moment the setting was
  added. Also worth noting: the failure mode of a missing env var is *silence*, not an error, so
  "it started up fine" proves nothing about configuration.
