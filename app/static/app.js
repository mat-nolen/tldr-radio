/* TLDR Radio — shared mockup behavior.
   Everything visual reads the CSS design tokens so it re-themes for free.
   No dependencies, no CDN. Guards for elements that aren't on every page. */

/* ---- Edition catalog (shared by every page) -------------------------------
   One fetch of /api/editions, cached for the life of the page. The Desk, the Library and
   Settings all read names and colours from here, so adding a newsletter never means editing
   a lookup table in three places. */
window.TLDR = window.TLDR || {};
(function (TLDR) {
  let pending = null;
  TLDR.names = {};
  /* Usable before the catalog resolves — falls back to a title-cased slug, matching
     name_for() on the server. */
  TLDR.title = slug => TLDR.names[slug] || `TLDR ${slug.charAt(0).toUpperCase() + slug.slice(1)}`;
  TLDR.catalog = function () {
    if (!pending) {
      pending = fetch('/api/editions')
        .then(r => r.json())
        .then(data => {
          data.editions.forEach(e => { TLDR.names[e.slug] = e.name; });
          return data;
        })
        .catch(() => ({ editions: [], auto_audio_seconds: null }));
    }
    return pending;
  };
})(window.TLDR);

(function () {
  const root = document.documentElement;

  /* ---- Theme toggle (Daytime Paper ⟷ Night Broadcast) --------------------- */
  function currentTheme() {
    const set = root.getAttribute('data-theme');
    if (set) return set;
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  function applyTheme(t) {
    root.setAttribute('data-theme', t);
    try { localStorage.setItem('tldr-theme', t); } catch (e) {}
    document.querySelectorAll('[data-theme-label]').forEach(el => {
      el.textContent = t === 'dark' ? 'Night' : 'Daytime';
    });
    // let token-driven visuals recolor
    window.dispatchEvent(new CustomEvent('themechange'));
  }
  try {
    const saved = localStorage.getItem('tldr-theme');
    if (saved) root.setAttribute('data-theme', saved);
  } catch (e) {}
  document.querySelectorAll('[data-theme-toggle]').forEach(btn => {
    btn.addEventListener('click', () => applyTheme(currentTheme() === 'dark' ? 'light' : 'dark'));
  });
  document.querySelectorAll('[data-theme-label]').forEach(el => {
    el.textContent = currentTheme() === 'dark' ? 'Night' : 'Daytime';
  });

  /* Station toggles are delegated in the Desk block below — they're rendered from the
     catalog, so there is nothing to bind at load. */

  /* VU waveform, tuner seek, and chapter selection are owned by the Player block below (real audio). */
})();

/* ============================================================================
   Index — broadcast + live queue (SSE)
   ============================================================================ */
(function () {
  const queue = document.getElementById('queue');
  if (!queue) return; // not the Desk

  const dateInput = document.getElementById('date');
  const broadcastBtn = document.getElementById('broadcast');
  const broadcastLabel = document.getElementById('broadcast-label');
  const onAir = document.getElementById('on-air');
  const emptyMsg = document.getElementById('queue-empty');
  const WORKING = ['fetching', 'parsing', 'scripting', 'synthesizing'];
  const jobsById = new Map();

  if (dateInput) {
    const t = new Date();
    dateInput.value = `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`;
  }

  const stationList = document.getElementById('stations');

  const selectedEditions = () =>
    [...document.querySelectorAll('.station[aria-pressed="true"]')].map(b => b.dataset.edition);

  function updateLabel() {
    const n = selectedEditions().length;
    if (broadcastLabel) broadcastLabel.textContent = n ? `Broadcast ${n} edition${n > 1 ? 's' : ''}` : 'Pick a station';
    if (broadcastBtn) broadcastBtn.disabled = n === 0;
  }

  /* Stations come from Settings, so the Desk only ever offers what you've switched on.
     The ones that also run overnight start pressed — the common case is "broadcast today's
     usual set", and an edition you keep on the Desk for the occasional manual run doesn't
     get swept in by accident. */
  window.TLDR.catalog().then(data => {
    if (!stationList) return;
    const desk = data.editions.filter(e => e.desk);
    if (!desk.length) {
      stationList.innerHTML =
        '<p class="muted mono">No stations enabled — pick some in <a href="settings.html">Settings</a>.</p>';
      updateLabel();
      return;
    }
    stationList.innerHTML = desk.map(e => `
      <button class="station" style="--st:var(--edition-${e.slug})" aria-pressed="${e.auto}" data-edition="${e.slug}">
        <span class="row"><span class="dot"></span> ${e.name}</span>
        <span class="mono muted">${e.tagline}</span>
      </button>`).join('');
    updateLabel();
  });

  if (stationList) {
    stationList.addEventListener('click', e => {
      const btn = e.target.closest('.station');
      if (!btn) return;
      btn.setAttribute('aria-pressed', String(btn.getAttribute('aria-pressed') !== 'true'));
      updateLabel();
    });
  }
  updateLabel();

  const anyWorking = () => [...jobsById.values()].some(j => WORKING.includes(j.status));
  const titleFor = window.TLDR.title;

  function render(job) {
    jobsById.set(job.id, job);
    if (emptyMsg) emptyMsg.hidden = true;
    let el = document.getElementById(`job-${job.id}`);
    if (!el) {
      el = document.createElement('article');
      el.className = 'card';
      el.id = `job-${job.id}`;
      queue.appendChild(el);
    }
    const [done, total] = job.progress || [0, 0];
    let right = '';
    if (job.status === 'synthesizing') {
      const pct = total ? Math.round((done / total) * 100) : 0;
      right = `<div class="q-meter meter"><i style="width:${pct}%"></i></div>`;
    } else if (job.status === 'ready') {
      right = `<a class="mono" href="player.html?ep=${job.episode_id}">open episode →</a>`;
    } else if (job.status === 'failed') {
      right = `<button class="btn btn-ghost" data-retry="${job.id}">Retry</button>`;
    } else if (job.status === 'skipped') {
      right = `<button class="btn btn-ghost" data-retry="${job.id}">Check again</button>`;
    }
    const cls = job.status === 'ready' ? 'status-ready'
      : job.status === 'failed' ? 'status-failed'
      : job.status === 'skipped' ? 'status-skipped'
      : WORKING.includes(job.status) ? 'status-working' : 'status-queued';
    const label = job.status === 'synthesizing' ? `synthesizing ${done}/${total}`
      : job.status === 'skipped' ? 'not published'
      : job.status;
    el.innerHTML = `<div class="q-row">
        <div class="q-main">
          <span class="badge" style="--st:var(--edition-${job.edition})">${job.edition.toUpperCase()}</span>
          <strong>${titleFor(job.edition)} — ${job.issue_date}</strong>
          <span class="status ${cls}">${label}</span>
        </div>
        ${right}
        ${job.error ? `<div class="q-meter banner banner-err"><span>${job.error}</span></div>` : ''}
        ${job.note ? `<div class="q-meter mono muted q-note">${job.note}</div>` : ''}
      </div>`;
    if (onAir) onAir.hidden = !anyWorking();
  }

  if (broadcastBtn) {
    broadcastBtn.addEventListener('click', async () => {
      const editions = selectedEditions();
      if (!editions.length) return;
      // Plex guard: synthesis and Plex transcoding share the CPU — warn if streaming.
      try {
        const px = await fetch('/api/plex/status').then(r => r.json());
        if (px.playing) {
          const who = px.sessions.map(s => s.title).filter(Boolean).slice(0, 3).join(', ');
          const ok = confirm(
            `Plex is streaming right now (${px.count})${who ? ':\n  ' + who : ''}.\n\n` +
            'Synthesizing uses the CPU and could cause buffering. Broadcast anyway?'
          );
          if (!ok) return;
        }
      } catch (_) { /* Plex unreachable — never block a broadcast */ }
      broadcastBtn.disabled = true;
      try {
        await fetch('/api/jobs', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ editions, date: dateInput.value }),
        });
      } finally {
        updateLabel();
      }
    });
  }

  queue.addEventListener('click', async e => {
    const btn = e.target.closest('[data-retry]');
    if (!btn) return;
    await fetch(`/api/jobs/${btn.dataset.retry}/retry`, { method: 'POST' });
  });

  const es = new EventSource('/api/jobs/stream');
  es.onmessage = ev => {
    try { render(JSON.parse(ev.data)); } catch (_) { /* ignore malformed */ }
  };
})();

/* ============================================================================
   Settings — stations (which newsletters the Desk offers / the night run builds)
   ============================================================================ */
(function () {
  const list = document.getElementById('station-list');
  if (!list) return; // not Settings

  const saveBtn = document.getElementById('save-stations');
  const estimate = document.getElementById('station-estimate');
  let means = {};        // slug -> mean episode length, for editions that have run
  let fallback = null;   // their average, borrowed by editions that haven't

  const picked = kind =>
    [...list.querySelectorAll(`input[data-kind="${kind}"]:checked`)].map(i => i.dataset.slug);

  const hm = s => {
    const m = Math.round(s / 60);
    return m >= 60 ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m` : `${m}m`;
  };

  /* Estimates the *audio*, not the wall clock: synthesis runs slower than realtime on a slow
     box and one episode at a time, so the night run always takes longer than this. Stated as
     listening time because that is the number we can actually measure. */
  function updateEstimate() {
    const auto = picked('auto'), desk = picked('desk').length;
    const head = `${desk} on the Desk · ${auto.length} overnight`;
    if (!auto.length) { estimate.textContent = `${desk} on the Desk · nothing runs overnight.`; return; }
    if (fallback === null) { estimate.textContent = `${head}.`; return; }
    const secs = auto.reduce((total, slug) => total + (means[slug] ?? fallback), 0);
    estimate.textContent = `${head} ≈ ${hm(secs)} of audio a night, built one edition at a time.`;
  }

  window.TLDR.catalog().then(data => {
    means = Object.fromEntries(
      data.editions.filter(e => e.mean_duration_seconds).map(e => [e.slug, e.mean_duration_seconds])
    );
    const seen = Object.values(means);
    fallback = seen.length ? seen.reduce((a, b) => a + b, 0) / seen.length : null;
    list.innerHTML = data.editions.map(e => `
      <div class="st-row" style="--st:var(--edition-${e.slug})">
        <div>
          <div class="st-name"><i></i>${e.name}</div>
          <div class="st-tag">${e.tagline}</div>
        </div>
        <p class="st-desc">${e.description}</p>
        <div class="st-col"><input type="checkbox" data-kind="desk" data-slug="${e.slug}"
          ${e.desk ? 'checked' : ''} aria-label="Show ${e.name} on the Desk"></div>
        <div class="st-col"><input type="checkbox" data-kind="auto" data-slug="${e.slug}"
          ${e.auto ? 'checked' : ''} aria-label="Broadcast ${e.name} overnight"></div>
      </div>`).join('');
    updateEstimate();
  });

  // Keep the auto ⊆ desk invariant visible in the UI rather than silently repairing it on
  // the server: ticking Auto turns Desk on, and clearing Desk clears Auto with it.
  list.addEventListener('change', e => {
    const box = e.target.closest('input[type="checkbox"]');
    if (!box) return;
    const partner = list.querySelector(
      `input[data-kind="${box.dataset.kind === 'auto' ? 'desk' : 'auto'}"][data-slug="${box.dataset.slug}"]`
    );
    if (partner) {
      if (box.dataset.kind === 'auto' && box.checked) partner.checked = true;
      if (box.dataset.kind === 'desk' && !box.checked) partner.checked = false;
    }
    updateEstimate();
  });

  if (saveBtn) {
    saveBtn.addEventListener('click', async () => {
      saveBtn.disabled = true;
      try {
        await fetch('/api/settings', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ desk_editions: picked('desk'), auto_editions: picked('auto') }),
        });
        saveBtn.textContent = 'Saved ✓';
        setTimeout(() => (saveBtn.textContent = 'Save stations'), 1500);
      } finally {
        saveBtn.disabled = false;
      }
    });
  }
})();

/* ============================================================================
   Settings — voice picker, audition, save, retention
   ============================================================================ */
(function () {
  const list = document.getElementById('voice-list');
  if (!list) return; // not Settings

  const saveVoice = document.getElementById('save-voice');
  const retention = document.getElementById('retention');
  const saveRetention = document.getElementById('save-retention');
  // Attached to the DOM (hidden) so QA can assert on playback state — a bare `new Audio()`
  // is invisible to Playwright.
  const audio = new Audio();
  audio.id = 'audition-audio';
  audio.hidden = true;
  document.body.appendChild(audio);

  const LANG = { a: 'American English', b: 'British English', e: 'Spanish', f: 'French', h: 'Hindi', i: 'Italian', j: 'Japanese', p: 'Portuguese', z: 'Mandarin' };
  const GENDER = { f: 'Female', m: 'Male' };
  const groupOf = id => {
    const lang = LANG[id[0]] || 'Other';
    const g = GENDER[id[1]];
    return g ? `${lang} · ${g}` : lang;
  };
  const nameOf = id => {
    const base = id.includes('_') ? id.split('_').slice(1).join('_') : id;
    return base.charAt(0).toUpperCase() + base.slice(1);
  };
  const PLAY_ICON = '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg> Audition';

  async function load() {
    const [settings, voicesResp] = await Promise.all([
      fetch('/api/settings').then(r => r.json()),
      fetch('/api/voices').then(r => r.json()),
    ]);
    if (retention) retention.value = settings.retention_days;
    const current = settings.default_voice;
    const voices = (voicesResp.voices || []).map(v => (typeof v === 'string' ? v : v.id || v.name));
    const groups = {};
    for (const id of voices) (groups[groupOf(id)] ||= []).push(id);
    list.innerHTML = '';
    for (const [group, ids] of Object.entries(groups)) {
      const g = document.createElement('div');
      g.className = 'voice-group';
      g.innerHTML = `<p class="rule-label">${group}</p>`;
      for (const id of ids.sort()) {
        const row = document.createElement('div');
        row.className = 'voice-row';
        row.innerHTML =
          `<label class="pick"><input type="radio" name="voice" value="${id}" ${id === current ? 'checked' : ''}></label>` +
          `<div><span class="voice-name">${nameOf(id)}</span> &nbsp; <span class="voice-id">${id}</span></div>` +
          `<button class="audition" data-voice="${id}">${PLAY_ICON}</button>`;
        g.appendChild(row);
      }
      list.appendChild(g);
    }
  }

  // 8 ms of silence, 8 kHz mono 8-bit PCM. Playing this *inside* the click unlocks the audio
  // element; after that the element will accept a programmatic src + play() with no further
  // user gesture, which frees us to await the synthesis instead of making the media loader
  // sit through it.
  const SILENT_WAV = 'data:audio/wav;base64,UklGRmQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YUAAAACAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICA';
  // A first synth is seconds on this Mac but minutes are possible on a slow box; this only
  // exists so a hang eventually says something instead of spinning forever.
  const AUDITION_TIMEOUT_MS = 120000;
  let auditionUrl = null;   // object URL of the clip currently loaded

  /** Turn a failed audition into something the user can act on. */
  function reportAuditionFailure(btn, err) {
    console.error('Audition failed:', err);
    const why = err.name && err.name !== 'Error' ? `${err.message} (${err.name})` : err.message;
    btn.title = 'Audition failed — ' + why;
    btn.dataset.failed = 'true';
  }

  // Two-step, and the order matters.
  //
  // v0.7.2 made playback start inside the click so the autoplay policy couldn't reject it, which
  // was right — but it left the <audio> element owning the wait for an on-demand synth. WebKit
  // abandons a media load that stalls for ~16 s and reports MEDIA_ERR_SRC_NOT_SUPPORTED while
  // still saying `paused: false`, so it reads as pure silence; Chromium waits happily, which is
  // why three Chromium environments never reproduced it. Reproduced 2026-08-07 in Playwright
  // WebKit at an 18 s stall — see lessons_learned.md.
  //
  // So: unlock the element on the gesture with a silent clip (no network), then fetch the real
  // one at our leisure and hand the element a blob it can decode immediately. The media loader
  // never waits on the server, so the synth can take as long as it likes.
  list.addEventListener('click', async e => {
    const btn = e.target.closest('.audition');
    if (!btn) return;
    const url = '/api/voices/audition/' + encodeURIComponent(btn.dataset.voice);

    // Everything up to the first await must stay synchronous — the gesture is spent otherwise.
    audio.src = SILENT_WAV;
    const unlock = audio.play().catch(() => {});  // settles either way; never rejects outward

    btn.disabled = true;
    btn.textContent = 'Preparing…';
    btn.removeAttribute('title');
    delete btn.dataset.failed;

    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), AUDITION_TIMEOUT_MS);
    try {
      await unlock;
      const resp = await fetch(url, { signal: abort.signal });
      if (!resp.ok) throw new Error(`HTTP ${resp.status} ${resp.statusText}`);
      const type = resp.headers.get('content-type') || '';
      if (!type.startsWith('audio/')) throw new Error(`server sent ${type || 'no content-type'}`);
      const blob = await resp.blob();
      if (auditionUrl) URL.revokeObjectURL(auditionUrl);
      auditionUrl = URL.createObjectURL(blob);
      // currentSrc is a blob: URL now and names nothing — keep the voice legible for QA.
      audio.dataset.voice = btn.dataset.voice;
      audio.src = auditionUrl;
      await audio.play();
    } catch (err) {
      if (err.name === 'AbortError') {  // our timeout, or superseded by a later click
        if (abort.signal.aborted) {
          reportAuditionFailure(btn, new Error(`no response in ${AUDITION_TIMEOUT_MS / 1000}s`));
        }
      } else {
        reportAuditionFailure(btn, err);
      }
    } finally {
      clearTimeout(timer);
      btn.disabled = false;
      btn.innerHTML = PLAY_ICON;
    }
  });

  const flash = (btn, orig) => { btn.textContent = 'Saved ✓'; setTimeout(() => (btn.textContent = orig), 1500); };

  if (saveVoice) {
    saveVoice.addEventListener('click', async () => {
      const sel = document.querySelector('input[name="voice"]:checked');
      if (!sel) return;
      await fetch('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ default_voice: sel.value }),
      });
      flash(saveVoice, 'Save default voice');
    });
  }
  if (saveRetention) {
    saveRetention.addEventListener('click', async () => {
      await fetch('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ retention_days: parseInt(retention.value, 10) }),
      });
      flash(saveRetention, 'Save retention');
    });
  }

  load();
})();

/* ============================================================================
   Player — library, chapters, transport bound to real audio, resume, keyboard
   ============================================================================ */
(function () {
  const library = document.getElementById('library');
  const chaptersEl = document.getElementById('chapters');
  if (!library && !chaptersEl) return; // not the Player

  const audio = new Audio();
  const tuner = document.querySelector('.tuner');
  const needle = tuner ? tuner.querySelector('.needle') : null;
  const led = document.querySelector('[data-led]');
  const totalEl = document.querySelector('.time-readout .total');
  const speed = document.querySelector('.speed');
  const titleEl = document.getElementById('ep-title');
  const subEl = document.getElementById('ep-sub');
  const voiceEl = document.getElementById('ep-voice');
  const [prevBtn, back15, playBtn, fwd15, nextBtn] = document.querySelectorAll('.transport .icon-btn');
  const nowChapter = document.getElementById('now-chapter');
  const ncSection = document.getElementById('nc-section');
  const ncPos = document.getElementById('nc-pos');
  const ncTitle = document.getElementById('nc-title');
  const ncSource = document.getElementById('nc-source');
  const ncMeta = document.getElementById('nc-meta');
  const ncLink = document.getElementById('nc-link');

  const PLAY = '<svg viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M8 5v14l11-7z"/></svg>';
  const PAUSE = '<svg viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M7 5h4v14H7zM13 5h4v14h-4z"/></svg>';

  // Progress waveform: static bars that fill with the signal colour as the chapter plays.
  const vu = document.querySelector('canvas.vu');
  let vuBars = [];
  function vuResize() {
    if (!vu) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = vu.clientWidth, h = vu.clientHeight;
    vu.width = Math.round(w * dpr);
    vu.height = Math.round(h * dpr);
    vu.getContext('2d').setTransform(dpr, 0, 0, dpr, 0, 0);
    const n = Math.max(24, Math.floor(w / 7));
    vuBars = Array.from({ length: n }, (_, i) => Math.abs(Math.sin(i * 0.7) * 0.6 + Math.sin(i * 0.23) * 0.3) * 0.7 + 0.15);
  }
  function drawWave(progress) {
    if (!vu || !vuBars.length) return;
    const ctx = vu.getContext('2d');
    const w = vu.clientWidth, h = vu.clientHeight;
    const css = getComputedStyle(document.documentElement);
    const signal = css.getPropertyValue('--signal').trim() || '#E2571E';
    const rule = css.getPropertyValue('--rule').trim() || '#D6CBB4';
    const n = vuBars.length, gap = 3, bw = (w - gap * (n - 1)) / n;
    const head = Math.round((progress || 0) * n);
    ctx.clearRect(0, 0, w, h);
    for (let i = 0; i < n; i++) {
      const bh = Math.max(2, vuBars[i] * (h - 4));
      const x = i * (bw + gap), y = (h - bh) / 2;
      const played = i < head;
      ctx.fillStyle = played ? signal : rule;
      ctx.globalAlpha = played ? 0.95 : 0.4;
      ctx.fillRect(x, y, bw, bh);
    }
    ctx.globalAlpha = 1;
  }
  const wprog = () => (audio.duration ? audio.currentTime / audio.duration : 0);
  vuResize();
  drawWave(0);
  window.addEventListener('resize', () => { vuResize(); drawWave(wprog()); });
  window.addEventListener('themechange', () => drawWave(wprog()));

  let ep = null;        // {episode, chapters, playback}
  let order = [];       // chapter idx order
  let current = null;   // current chapter idx
  let chapterInfo = new Map();  // idx -> {chapter, head, pos} for the now-playing card
  let saveTimer = 0;

  const fmt = s => { s = Math.max(0, Math.round(s || 0)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
  const esc = s => (s || '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const epTitle = window.TLDR.title;
  const dayLabel = iso => new Date(iso + 'T00:00:00').toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }).toUpperCase();

  function highlightEp(id) {
    library.querySelectorAll('.ep-card').forEach(c => {
      if (Number(c.dataset.ep) === Number(id)) c.setAttribute('aria-current', 'true');
      else c.removeAttribute('aria-current');
    });
    // Mark the day that holds it, so a collapsed rail still says where you are.
    const on = library.querySelector('.ep-card[aria-current="true"]');
    const owner = on ? on.closest('.day-group') : null;
    library.querySelectorAll('.day-group').forEach(g => {
      if (g === owner) g.dataset.playing = 'true';
      else delete g.dataset.playing;
    });
  }

  /* Day groups collapse by default and remember what you opened. Only expanded
     dates are stored; anything absent is collapsed, so the default needs no entry. */
  const DAY_KEY = 'tldr-open-days';
  function openDays() {
    try { return new Set(JSON.parse(localStorage.getItem(DAY_KEY) || '[]')); }
    catch (e) { return new Set(); }
  }
  function storeOpenDays(set) {
    try { localStorage.setItem(DAY_KEY, JSON.stringify([...set])); } catch (e) {}
  }
  function setDayOpen(group, isOpen, persist) {
    group.querySelector('.day-toggle').setAttribute('aria-expanded', String(isOpen));
    group.querySelector('.day-eps').hidden = !isOpen;
    if (!persist) return;
    const open = openDays();
    if (isOpen) open.add(group.dataset.date); else open.delete(group.dataset.date);
    storeOpenDays(open);
  }

  library?.addEventListener('click', e => {
    const btn = e.target.closest('.day-toggle');
    if (!btn) return;
    const group = btn.closest('.day-group');
    setDayOpen(group, btn.getAttribute('aria-expanded') !== 'true', true);
  });

  async function loadLibrary(selectId) {
    // Catalog first — episode titles come from it, so rendering before it lands would show
    // title-cased slugs and then silently change under the reader.
    const [eps] = await Promise.all([
      fetch('/api/episodes').then(r => r.json()),
      window.TLDR.catalog(),
    ]);
    library.innerHTML = '';
    if (!eps.length) { library.innerHTML = '<p class="muted mono">No episodes yet — broadcast one on the Desk.</p>'; return eps; }
    const byDate = {};
    for (const e of eps) (byDate[e.issue_date] ||= []).push(e);
    // Retention drops old days; drop their stored state too rather than growing forever.
    const open = openDays();
    const live = new Set(Object.keys(byDate).filter(d => open.has(d)));
    if (live.size !== open.size) storeOpenDays(live);
    for (const date of Object.keys(byDate)) {
      const group = document.createElement('div');
      group.className = 'day-group';
      group.dataset.date = date;
      const panelId = `day-${date}`;
      // Capped: with 14 editions enabled a full day would push the date and count out of a
      // 340px rail. The count beside them is the authoritative total.
      const dots = byDate[date].slice(0, 8)
        .map(e => `<i style="--st:var(--edition-${e.edition})"></i>`).join('');
      group.innerHTML =
        `<button class="day-toggle" type="button" aria-expanded="false" aria-controls="${panelId}">
           <svg class="chev" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5l7 7-7 7"/></svg>
           <span class="mono day-date">${dayLabel(date)}</span>
           <span class="day-line"></span>
           <span class="day-dots" aria-hidden="true">${dots}</span>
           <span class="mono day-count">${byDate[date].length}</span>
         </button>
         <div class="day-eps" id="${panelId}" hidden></div>`;
      const dayEps = group.querySelector('.day-eps');
      for (const e of byDate[date]) {
        const row = document.createElement('div');
        row.className = 'ep-row';
        const a = document.createElement('a');
        a.className = 'card ep-card';
        a.href = `player.html?ep=${e.id}`;
        a.dataset.ep = e.id;
        a.innerHTML = `<div class="ep-top"><span class="ep-title">${epTitle(e.edition)}</span><span class="badge" style="--st:var(--edition-${e.edition})">${e.edition.toUpperCase()}</span></div>
          <div class="ep-meta mono muted">${e.story_count} stories · ${fmt(e.duration_seconds)} · ${e.status}</div>`;
        a.addEventListener('click', ev => { ev.preventDefault(); history.replaceState(null, '', `player.html?ep=${e.id}`); selectEpisode(e.id); });
        const del = document.createElement('button');
        del.className = 'ep-del';
        del.dataset.del = e.id;
        del.dataset.label = `${epTitle(e.edition)} — ${e.issue_date}`;
        del.title = 'Delete episode';
        del.setAttribute('aria-label', `Delete ${epTitle(e.edition)} — ${e.issue_date}`);
        del.innerHTML = '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>';
        row.append(a, del);
        dayEps.appendChild(row);
      }
      if (live.has(date)) setDayOpen(group, true, false);
      library.appendChild(group);
    }
    highlightEp(selectId);
    return eps;
  }

  async function selectEpisode(id) {
    if (titleEl) titleEl.textContent = 'Loading…';
    if (subEl) subEl.textContent = '';
    chaptersEl.innerHTML = '';
    chapterInfo = new Map();
    renderNowChapter(null);
    const data = await fetch(`/api/episodes/${id}`).then(r => r.json());
    if (!data.episode) return;
    ep = data;
    order = ep.chapters.map(c => c.idx);
    if (titleEl) titleEl.textContent = ep.episode.title;
    if (subEl) subEl.textContent = `${ep.episode.story_count} stories · ${fmt(ep.episode.duration_seconds)} total`;
    if (voiceEl) voiceEl.textContent = `VOICE · ${ep.episode.voice}`;
    renderChapters();
    highlightEp(id);
    const pb = ep.playback;
    loadChapter(pb ? pb.chapter_idx : order[0], pb ? pb.position_seconds : 0, false);
  }

  /** Blank the console — used when the episode it was showing no longer exists. */
  function clearConsole() {
    audio.pause();
    audio.removeAttribute('src');
    audio.load();
    if (saveTimer) { clearTimeout(saveTimer); saveTimer = 0; }  // never PUT playback for a deleted ep
    ep = null; order = []; current = null;
    chaptersEl.innerHTML = '';
    chapterInfo = new Map();
    renderNowChapter(null);
    if (titleEl) titleEl.textContent = 'No episode selected';
    if (subEl) subEl.textContent = '';
    if (voiceEl) voiceEl.textContent = '';
    if (led) led.textContent = fmt(0);
    if (totalEl) totalEl.textContent = ' / ' + fmt(0);
    if (needle) needle.style.left = '0%';
    drawWave(0);
  }

  async function deleteEpisode(id, label) {
    const ok = confirm(
      `Delete ${label}?\n\nThe episode and its audio are removed from disk. ` +
      'You can re-broadcast it from the Desk.'
    );
    if (!ok) return;
    const wasCurrent = ep && Number(ep.episode.id) === Number(id);
    if (wasCurrent) clearConsole();
    await fetch(`/api/episodes/${id}`, { method: 'DELETE' });
    const eps = await loadLibrary(ep ? ep.episode.id : null);
    if (wasCurrent) {
      history.replaceState(null, '', 'player.html');
      if (eps.length) selectEpisode(eps[0].id);
    }
  }

  library?.addEventListener('click', e => {
    const del = e.target.closest('[data-del]');
    if (!del) return;
    e.preventDefault();
    deleteEpisode(Number(del.dataset.del), del.dataset.label);
  });

  function renderChapters() {
    chaptersEl.innerHTML = '';
    chapterInfo = new Map();
    const stories = ep.chapters.filter(c => c.kind === 'story').length;
    let lastSection = null, n = 0;
    for (const c of ep.chapters) {
      if (c.kind === 'story' && c.section && c.section !== lastSection) {
        lastSection = c.section;
        const d = document.createElement('p');
        d.className = 'rule-label';
        d.textContent = c.section;
        chaptersEl.appendChild(d);
      }
      const num = c.kind === 'story' ? String(++n).padStart(2, '0') : (c.kind === 'intro' ? '▶' : '■');
      const head = c.kind === 'story' ? c.headline : (c.kind === 'intro' ? 'Intro' : 'Outro');
      chapterInfo.set(c.idx, {
        chapter: c,
        head,
        pos: c.kind === 'story' ? `STORY ${n} / ${stories}` : c.kind.toUpperCase(),
      });
      const src = c.summary_source ? `<details><summary>Show source</summary><p>${esc(c.summary_source)}</p></details>` : '';
      const saves = c.read_time ? ` <span class="muted">· saves ~${parseInt(c.read_time, 10)}m</span>` : '';
      const timing = c.duration_seconds
        ? `<span class="mono" title="clip length · time saved vs reading the full article">${fmt(c.duration_seconds)}${saves}</span>`
        : '';
      const link = c.url ? `<a class="readmore" href="${c.url}" target="_blank" rel="noopener">read more ↗</a>` : '';
      const row = document.createElement('div');
      row.className = 'chapter';
      row.dataset.idx = c.idx;
      row.innerHTML = `<span class="num">${num}</span><div><h4>${esc(head)}</h4>${src}</div><div class="side">${timing}${link}</div>`;
      row.addEventListener('click', ev => { if (ev.target.closest('a, summary, details')) return; loadChapter(c.idx, 0, true); });
      chaptersEl.appendChild(row);
    }
  }

  /** Mirror the playing chapter into the card under the transport, so the story you
      are hearing is on screen without scrolling to find it in the list. */
  function renderNowChapter(idx) {
    if (!nowChapter) return;
    const info = chapterInfo.get(Number(idx));
    if (!info) { nowChapter.hidden = true; return; }
    const c = info.chapter;
    ncSection.textContent = c.section || '';
    ncPos.textContent = info.pos;
    ncTitle.textContent = info.head;
    ncSource.textContent = c.summary_source || '';
    ncSource.hidden = !c.summary_source;
    const bits = [];
    if (c.duration_seconds) bits.push(fmt(c.duration_seconds));
    if (c.read_time) bits.push(`saves ~${parseInt(c.read_time, 10)}m vs reading`);
    ncMeta.textContent = bits.join(' · ');
    if (c.url) { ncLink.href = c.url; ncLink.hidden = false; } else { ncLink.hidden = true; }
    nowChapter.hidden = false;
  }

  const highlightChapter = idx => {
    chaptersEl.querySelectorAll('.chapter').forEach(r => {
      if (Number(r.dataset.idx) === Number(idx)) r.setAttribute('data-current', 'true');
      else r.removeAttribute('data-current');
    });
    renderNowChapter(idx);
  };

  function loadChapter(idx, position, autoplay) {
    if (!order.includes(idx)) return;
    current = idx;
    highlightChapter(idx);
    audio.src = `/api/audio/${ep.episode.id}/${idx}`;
    drawWave(0);
    audio.onloadedmetadata = () => {
      audio.currentTime = position || 0;
      if (autoplay) audio.play();
    };
  }

  const step = d => { const i = order.indexOf(current); const j = i + d; if (j >= 0 && j < order.length) loadChapter(order[j], 0, true); };

  playBtn?.addEventListener('click', () => (audio.paused ? audio.play() : audio.pause()));
  prevBtn?.addEventListener('click', () => step(-1));
  nextBtn?.addEventListener('click', () => step(1));
  back15?.addEventListener('click', () => (audio.currentTime = Math.max(0, audio.currentTime - 15)));
  fwd15?.addEventListener('click', () => (audio.currentTime = Math.min(audio.duration || 0, audio.currentTime + 15)));
  if (speed) { audio.playbackRate = parseFloat(speed.value); speed.addEventListener('change', () => (audio.playbackRate = parseFloat(speed.value))); }

  audio.addEventListener('play', () => playBtn && (playBtn.innerHTML = PAUSE));
  audio.addEventListener('pause', () => playBtn && (playBtn.innerHTML = PLAY));
  audio.addEventListener('ended', () => step(1));
  audio.addEventListener('timeupdate', () => {
    const d = audio.duration || 0;
    if (needle && d) needle.style.left = (audio.currentTime / d) * 100 + '%';
    if (led) led.textContent = fmt(audio.currentTime);
    if (totalEl) totalEl.textContent = ' / ' + fmt(d);
    drawWave(d ? audio.currentTime / d : 0);
    savePlayback();
  });

  if (tuner) {
    tuner.addEventListener('pointerdown', e => {
      const seek = x => { const r = tuner.getBoundingClientRect(); const p = Math.min(1, Math.max(0, (x - r.left) / r.width)); if (audio.duration) audio.currentTime = p * audio.duration; };
      seek(e.clientX);
      const move = ev => seek(ev.clientX);
      const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up);
    });
  }

  function savePlayback() {
    if (!ep || saveTimer) return;
    saveTimer = window.setTimeout(() => {
      saveTimer = 0;
      fetch(`/api/playback/${ep.episode.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chapter_idx: current, position_seconds: audio.currentTime }),
      }).catch(() => {});
    }, 3000);
  }

  window.addEventListener('keydown', e => {
    if (e.target.closest('input, select, textarea, button, summary, a')) return;
    if (e.key === ' ') { e.preventDefault(); audio.paused ? audio.play() : audio.pause(); }
    else if (e.key === 'ArrowLeft') audio.currentTime = Math.max(0, audio.currentTime - 15);
    else if (e.key === 'ArrowRight') audio.currentTime = Math.min(audio.duration || 0, audio.currentTime + 15);
    else if (e.key === '[') step(-1);
    else if (e.key === ']') step(1);
  });

  const want = new URLSearchParams(location.search).get('ep');
  loadLibrary(want).then(eps => {
    if (want) selectEpisode(Number(want));
    else if (eps && eps.length) selectEpisode(eps[0].id);
  });
})();
