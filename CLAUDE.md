# CLAUDE.md — Project Instructions

> Read automatically by [Claude Code](https://claude.com/claude-code). These are the rules and
> locked decisions for this project. If you're a human contributor, read it anyway — it explains
> *why* things are the way they are, which the code can't.

## Project Overview

- **Type:** Local, single-user web app. `localhost` only, no auth, no multi-tenancy.
- **Python:** 3.13 · **Web:** FastAPI + uvicorn, a background asyncio worker, SSE for live progress
- **Frontend:** Vanilla HTML/CSS/JS served by FastAPI — **no framework, no build step, no CDN**
- **Data:** stdlib `sqlite3` (5 tables, no ORM); mp3s + a page cache on a `/data` volume
- **TTS:** Kokoro-82M in Docker via Kokoro-FastAPI (`ghcr.io/remsky/kokoro-fastapi-cpu`),
  OpenAI-compatible `/v1/audio/speech`
- **Distribution:** Docker Compose (app + kokoro), localhost only

## Locked decisions — do not undo these without a very good reason

These aren't preferences; each one is load-bearing and reversing it breaks something real.

1. **No LLM at runtime, ever.** Parsing, cleanup and TTS-prep are all deterministic (regex +
   Kokoro's own normalizer). This is the project's core promise: what you hear is what TLDR
   published, never a summary or a paraphrase. Adding a model to "clean up" the text destroys it.
2. **Build-less frontend.** No framework, no bundler, no CDN, self-hosted fonts. The design system
   (`app/static/styles/`) is complete. Do not introduce React/Tailwind or a build step.
3. **The parser is the single point of failure — and that's deliberate.** There is no LLM repair
   fallback. It keys on page *structure*, never a hardcoded section list, so a new newsletter needs
   no code. When it can't parse, it must fail **loudly** with a story count rather than quietly
   emitting a short episode. `EmptyParseError` is reserved for exactly that; "not published" is a
   separate, quiet state.
4. **Editions are declared in one place:** `app/editions.py`. Adding a newsletter is a catalog
   entry plus two colour tokens in `design-tokens.css` — nothing else.
5. **$0 and no credentials.** No API keys, no accounts, no external services beyond the public
   `tldr.tech` archive and the local `kokoro` container. Anything that needs a key is out of scope.
6. **Cache every fetch to disk**, but never cache a response that isn't a real dated issue — a
   redirect to the landing page must not be stored under the issue's key.

## Working on it

- PEP 8 (Ruff enforced), type hints on signatures, `logging` not `print`, `pathlib.Path`
- Verify it **runs** before calling anything done
- Don't add dependencies without a good reason; the dependency list is deliberately small
- Preview the UI over HTTP (uvicorn), never `file://`

```bash
pytest                  # tests
ruff check .            # lint
python qa/qa.py         # Playwright UI QA — both themes, contrast, mobile overflow
python qa/audio_qa.py   # audio integrity sweep
```

`qa/qa.py` needs `playwright install chromium webkit` once. **WebKit is not optional** — one class
of bug in this app (a media element abandoning a stalled load) is invisible in Chromium and was
only ever caught in WebKit. See `docs/lessons_learned.md`.

## Architecture in one pass

```
tldr.tech/<edition>/<date>  →  fetch.py    (cached HTML, redirect detection)
                            →  parse.py    (structure-driven, sponsor-dropping, fail-loud)
                            →  script.py   (templated intro/outro, section glue, pronunciation)
                            →  synth.py    (Kokoro, N chapters in parallel)
                            →  SQLite + mp3s on /data  →  player
```

`worker.py` is a **single-consumer** queue: one edition at a time. That's intentional — it bounds
CPU use, and it's why `tldr.tech` never sees a burst of concurrent fetches (which it throttles, in
a way that is indistinguishable from "not published"). Don't parallelise it.

## Further reading

- `docs/spec.md` — the full specification
- `docs/lessons_learned.md` — bugs worth remembering, with root causes
- `CHANGELOG.md` — what shipped when
