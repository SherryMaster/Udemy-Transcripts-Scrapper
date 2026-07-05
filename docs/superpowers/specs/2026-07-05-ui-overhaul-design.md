# UI Overhaul — Design Spec

**Date:** 2026-07-05
**Status:** Approved (pending user spec review)
**Scope:** Replace the CustomTkinter GUI of the Udemy Transcript Scraper with a polished web-based desktop UI, fix the parallel-progress glitch, and add a per-lecture status box grid.

---

## 1. Goals

1. **Polish.** Move from "working" to a polished, professional app: Midnight Pro dark dashboard aesthetic, smooth transitions, clear visual hierarchy.
2. **Fix the parallelism glitch.** The current GUI races when multiple worker threads update progress — a shared per-section "completed counter" is mutated from several threads via `self.after(0, ...)`, producing incorrect/flickering counts. The new design eliminates the shared counter entirely.
3. **Per-lecture status boxes.** Under each section, render a grid of boxes — one per lecture — highlighted by state: pending (dim), in-progress (pulsing), success (green), skipped (muted blue), failed (red).
4. **Preserve existing logic.** `scraper.py` (parallel batch scraping, retries, browser-use integration) and `progress_tracker.py` (resume state) stay essentially unchanged.

**Non-goals (YAGNI):** no multi-course queue, no settings-file persistence, no light/dark toggle (Midnight Pro is the single theme), no credential/auth storage, no auto-export. Output remains plain `.txt` transcripts in the existing folder structure.

---

## 2. Key Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Tech stack | Web UI (HTML/CSS/JS frontend, Python backend) | Full CSS control; smooth animations; flexible grids; real polish. CustomTkinter's limits cap polish. |
| Frontend framework | Vanilla JS (no React/Vue) | The UI is one screen with a section list + box grid (~300 lines of rendering). A framework adds build tooling for no benefit. |
| Launch method | Desktop window via PyWebView | Preserves the "double-click `run.sh` → app window" feel. Web tech embedded in a native window. |
| Backend ↔ frontend | FastAPI + WebSocket (`/ws`) | Scraper threads emit discrete per-lecture events; JS processes them sequentially in its event loop → no races. Commands over HTTP POST. |
| Visual direction | Midnight Pro | Dark dashboard, indigo accent, soft shadows, SaaS feel. Easy on the eyes for long sessions. |
| Skipped-lecture state | Own neutral state (muted blue) | Distinguish "got transcript" from "had no captions/quiz" at a glance. 5 states total. |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────┐
│  app.py  (entry point / launcher)                       │
│    starts FastAPI/uvicorn in a background thread         │
│    opens a PyWebView window → http://localhost:8765      │
│    on close: stops server, joins threads                 │
└──────────────┬──────────────────────────────────────────┘
               │
   ┌───────────┴────────────┐
   ▼                        ▼
┌─────────────────┐   ┌──────────────────────────────────┐
│  backend/        │   │  frontend/  (static, served by   │
│  server.py       │   │  FastAPI at /)                   │
│  FastAPI app     │   │  index.html, app.js, style.css   │
│  + ScraperSession│   │  Midnight Pro theme              │
│  POST /api/start │◄──┤  WebSocket client (/ws)          │
│  POST /api/stop  │   │  vanilla JS, tiny render fn      │
│  POST /api/resume│   └──────────────────────────────────┘
│  POST /api/retry │
│  WS   /ws         │
└────┬─────────────┘
     │ events (JSON, one per lecture state change)
     ▼
┌─────────────────────────────────────────────────────────┐
│  scraper.py  (largely unchanged)                        │
│  progress_callback contract → clean event emitter        │
│  progress_tracker.py unchanged (resume state on disk)    │
└─────────────────────────────────────────────────────────┘
```

### Components

- **`app.py` (launcher, rewritten):** starts uvicorn in a daemon thread, opens a PyWebView window pointed at `http://localhost:8765`. On window close, signals stop, joins the scrape thread, and shuts the server down. Also installs a close-guard (confirm if running).
- **`backend/server.py` (new):** a thin FastAPI app. Owns one `ScraperSession` (a wrapper around `UdemyScraper` + `ProgressTracker` + the scrape thread). Serves the static frontend from `frontend/`, exposes the HTTP command endpoints, and maintains a single WebSocket connection. The session funnels scraper callbacks into WebSocket events. Commands run the scrape in a background thread; the WS runs on the server's event loop.
- **`scraper.py` (minimal change):** the `progress_callback` contract becomes `(event: dict)` instead of the positional `(section_idx, lecture_idx, status, message)`. The callback is invoked with a dict carrying the scraper's **native** status (`working`, `saved`, `skipped`, `failed`) plus `sectionIdx`, `lectureIdx`, `message`, and optional `size`. The parallel worker logic, batching, and `fetch_transcripts_batch` split-retry are untouched.
- **`backend/scraper_session.py` (new, the only glue):** adapts the existing `UdemyScraper.scrape_parallel` callback into the event protocol below. It normalizes the scraper's native statuses into the canonical **box-state vocabulary** (`working`→`in-progress`, `saved`→`success`, `skipped`→`skipped`, `failed`→`failed`) before emitting over the WebSocket. Holds the active thread, stop flag, and a reference to the WS sender.
- **`progress_tracker.py` (unchanged):** resume state on disk. Already handles corrupted files and resume correctly.
- **`frontend/index.html` + `app.js` + `style.css` (new):** the UI. `app.js` holds the state model, opens the WS, applies events to cells, and re-renders incrementally. `style.css` is the Midnight Pro theme.

### File structure (new)

```
app.py                      # rewritten: launcher
backend/
  server.py                 # FastAPI app + endpoints
  scraper_session.py        # scraper ↔ WS event adapter
scraper.py                  # minimal callback-contract change
progress_tracker.py         # unchanged
frontend/
  index.html
  app.js
  style.css
  dev-harness.html          # canned-event replay for visual verification
tests/
  test_progress_tracker.py
  test_scraper_events.py
  test_server.py
  test_extract_slug.py
run.sh                      # updated: activate venv, python app.py
requirements.txt            # new: fastapi, uvicorn, websockets, pywebview, customtkinter removed
```

---

## 4. UI Layout & Structure

Two-pane layout in a ~980×660 window (resizable, min 800×600).

### Left sidebar (300px, fixed)
1. **Brand** — logo mark + "Udemy Scraper" / "Transcript downloader"
2. **Configuration** card:
   - Course URL input (with inline validation)
   - Save-to input + Browse button
   - Batch size slider (1–15) with live value
   - Threads slider (1–6) with live value
   - Start (primary, indigo), Resume (ghost), Stop (red-outline) buttons
3. **Overall Progress** card:
   - Count `47 / 128`
   - Gradient progress bar
   - Stats badges: success / active / skipped / failed (each with colored dot + count)
   - Current-activity line: "Scraping: *lecture title*"
   - Elapsed + ETA

### Main area (flex)
1. **Header** — course title + subtitle (sections/lectures/workers) + live "RUNNING" pill with blinking dot (or "IDLE"/"DONE"/"STOPPED"/"ERROR" variants)
2. **Sections list** (scrollable) — one card per section:
   - Header: `01` index + section title + `count` + mini progress bar
   - Box grid (the new feature, §5)
   - Collapsible: click header to collapse the grid (for long courses)
3. **Activity Log bar** (bottom, collapsed by default) — click to expand a monospace log panel. Shows the latest line as a preview when collapsed.

### Empty state
Before any course loads, the main area shows a placeholder: "Paste a course URL to begin" with a small illustration — not a blank void.

---

## 5. Per-Lecture Status Box System

Each lecture is one cell in its section's grid. The grid wraps responsively (`grid-template-columns: repeat(auto-fill, minmax(16px, 1fr))`), so 100+ lecture courses lay out cleanly.

### States (5)

| State | Trigger | Visual |
|---|---|---|
| **pending** | default / not yet queued | dim slate (`rgba(148,163,184,.13)`) |
| **in-progress** | worker fetching this lecture | indigo with pulsing overlay (`@keyframes pulse`, 1.1s) |
| **success** | transcript saved | green (`#10b981`) |
| **skipped** | no captions / no English / quiz (api_error) | muted blue (`#3b82f6`, 55% opacity) |
| **failed** | error / vtt_error / empty | red (`#ef4444`) with soft glow |

State transitions cross-fade via CSS `transition` (not a hard flip) — this makes parallel updates *look* smooth even when 4 workers fire near-simultaneously.

### Interactions
- **Hover** a box → scales to 1.25× with an indigo focus ring; tooltip appears with: lecture title, status, transcript size, lecture `#n of N`, output filename.
- **Retry failed** — action in the sidebar progress card (and in the completion toast). Re-queues only `failed` lectures: they reset to `pending` → `in-progress` → terminal state. A section's failed count can reach zero this way.

### Why this fixes the glitch
Each cell is identified by `(sectionIdx, lectureIdx)`. A `lecture_status` event updates exactly one cell. The per-section and overall "completed" counts are **derived** by counting cells in each state — never mutated directly by worker threads. The JS event loop applies events sequentially to independent cells. There is no shared counter to race on.

---

## 6. Data Flow & Event Protocol

### Commands (frontend → backend, HTTP POST, JSON)

| Endpoint | Body | Effect |
|---|---|---|
| `POST /api/start` | `{url, outputDir, batchSize, numThreads}` | Fresh scrape: discover → scrape |
| `POST /api/resume` | `{outputDir}` | Load tracker state → discover → mark done lectures → continue |
| `POST /api/stop` | — | Set stop flag; workers finish current batch then exit |
| `POST /api/retry-failed` | — | Re-queue only `failed` lectures |

All command endpoints return `202 Accepted` (scraping is async; state comes over WS). Invalid input returns `400` with `{error}`.

### Events (backend → frontend, one WebSocket `/ws`)

```
course_discovered  { courseTitle, sections: [{ title, lectures:[{id,title}] }] }
lecture_status     { sectionIdx, lectureIdx, status, message, size? }
progress           { completed, total, failed, skipped, active, elapsedMs }
log                { message, level: info|success|warn|error }
done               { completed, failed, skipped }
error              { message }   // fatal
```

On WebSocket connect, the backend immediately emits a `progress` snapshot (current counts) so a reconnecting client can resync.

**`lecture_status.status` values** (canonical box-state vocabulary, set by `ScraperSession` after normalizing the scraper's native statuses):
`in-progress` | `success` | `skipped` | `failed`. The `pending` state is the default for cells not yet reported on — it is never sent in a `lecture_status` event; it is the initial value in the frontend model after `course_discovered`.

### Frontend state model (single source of truth)

```js
state = {
  phase: 'idle' | 'discovering' | 'running' | 'done' | 'stopped' | 'error',
  course: { title, sections: [{ index, title, lectures: [
              { index, id, title, status: 'pending', message, size } ] } ] },
  overall: { completed, total, failed, skipped, active, elapsedMs }
}
```

`app.js` applies a `lecture_status` event by locating `state.course.sections[sectionIdx].lectures[lectureIdx]`, setting `.status`/`.message`/`.size`, then re-rendering only that DOM cell (found via `data-sec`/`data-lec` attributes). Overall + section counts are recomputed from cell states.

### Resume flow
`POST /api/resume` → backend loads `ProgressTracker` state → calls `discover_course()` → emits `course_discovered` → emits a burst of `lecture_status` events marking already-completed lectures `success` and previously-failed lectures `failed` → continues scraping. The frontend hydrates the grid already-colored, then live updates take over.

### Lifecycle & button states
- `idle` → Start enabled; Resume enabled only if `tracker.is_resumable`; Stop disabled; inputs editable.
- `discovering`/`running` → Stop enabled; Start/Resume disabled; inputs locked; sliders read-only.
- `done`/`stopped`/`error` → Start re-enabled; Stop disabled; inputs unlocked; "Retry failed" enabled if `failed > 0`.

---

## 7. Polish & UX Additions

- **Empty state** — friendly placeholder before any course loads.
- **Completion toast** — slides in on finish: "✓ Done — 126 saved, 2 failed" with a "Retry failed" action button inside the toast.
- **Inline validation** — invalid URL shows a red hint under the input (replaces `messagebox.showwarning` modals).
- **Keyboard** — `Enter` in URL field starts; `Esc` stops.
- **Window-close guard** — if running, PyWebView confirms before closing (prevents orphaned `browser-use` processes).
- **Section collapse** — click a section header to collapse its box grid (long courses).
- **Focus & disabled states** — visible focus rings; clearly-degraded disabled styles.
- **Smooth state transitions** — CSS transitions on box color/scale.
- **Responsive box grid** — wraps to fill any window width.

---

## 8. Error Handling

| Failure | Handling |
|---|---|
| Browser not logged in / CDP unreachable | `connect_and_navigate` raises → `error` event → red banner with fix steps ("Open Chrome, log into Udemy, retry") |
| Invalid course URL | Inline validation blocks Start; no request sent |
| Network error mid-scrape | Lecture → `failed`; other workers continue. `fetch_transcripts_batch` already has split-retry. |
| WebSocket disconnects | Frontend shows "Reconnecting…" pill, auto-reconnects (helper pattern); on reconnect, server emits `progress` snapshot to resync |
| Resume state file corrupted | `ProgressTracker._load_state` catches `JSONDecodeError` → fresh state; proceeds as new scrape |
| Uvicorn/thread crash | `finally` in scrape runner emits `done`/`error`; UI returns to a safe `idle`/`error` state with the message shown |

---

## 9. Design Tokens — Midnight Pro

```css
--bg-deep:      #0b1120;   /* app background          */
--bg-card:      #0f172a;   /* section cards           */
--bg-elevated:  #1e293b;   /* inputs, log             */
--bg-sidebar:   #0d1424;
--border:       #1e293b;   /* hairlines               */
--border-strong:#334155;
--text-primary: #e2e8f0;
--text-secondary:#94a3b8;
--text-muted:   #64748b;
--accent:       #6366f1;   /* indigo                  */
--accent-hover: #818cf8;
--accent-glow:  rgba(99,102,241,.35);
--success:      #10b981;
--skip:         #3b82f6;
--fail:         #ef4444;
--pending:      rgba(148,163,184,.13);
```

Typography: system sans-serif (`-apple-system, "Segoe UI", Roboto, Inter`); monospace for the log (`"SF Mono", Consolas`). Gradients: indigo→violet (`#6366f1`→`#8b5cf6`) for the primary progress bar and logo.

---

## 10. Testing & Verification

The project currently has no tests. Introduce a small, targeted pytest suite.

### Backend tests (`tests/`)
- **`test_progress_tracker.py`** — real, no mocking: `init_course`, `mark_done`/`mark_failed`, `is_resumable`, corrupted-file fallback, resume hydration from a saved state.
- **`test_scraper_events.py`** — patch `subprocess.run` (mock `run_browser_use`) to return canned transcript results; assert `scrape_parallel` emits the correct `lecture_status` events per result status (`ok`→success, `no_captions`/`api_error`→skipped, `error`→failed); assert `stop_check` aborts cleanly after the current batch.
- **`test_server.py`** — FastAPI `TestClient`: `POST /api/start` with a mocked `ScraperSession` returns 202; the WS feed receives `course_discovered` then `lecture_status` events in order; `POST /api/retry-failed` re-queues only failed lectures.
- **`test_extract_slug.py`** — pure helpers `extract_course_slug` / `sanitize_filename` (cheap, high-value).

### Frontend verification
No unit framework. A `frontend/dev-harness.html` replays a canned sequence of WS events (a fake 3-section course with mixed outcomes) against the real `app.js` render logic, so box transitions, tooltips, retry, and the done-toast can be verified visually without running the real scraper. Doubles as a design-preview tool.

### Verification checklist (definition of "done")
1. `pytest` passes.
2. `run.sh` opens the PyWebView window showing the empty state.
3. Dev-harness replay shows all 5 box states + smooth transitions + tooltip + retry + toast.
4. Real run against a small Udemy course: boxes light up per-lecture with no flicker/race; Stop halts after the current batch; Resume recolors done lectures and continues; Retry-failed clears reds to green.
5. Close-during-run prompts confirmation; no orphaned `browser-use` processes remain.

### Lint/type
None currently configured. Optional `ruff` (not required for "done").

---

## 11. Migration Notes

- `requirements.txt` changes: add `fastapi`, `uvicorn[standard]`, `websockets`, `pywebview`; `customtkinter` is removed.
- `run.sh` is unchanged in spirit (`source venv/bin/activate && python app.py`).
- The old `app.py` (CustomTkinter) is replaced; the CustomTkinter `SectionProgress` class is deleted. Its responsibility moves to the frontend render function.
- `scraper.py`'s only breaking change is the `progress_callback` signature: from positional `(section_idx, lecture_idx, status, message)` to a single `event: dict`. The `scrape_parallel` `worker` function's callback invocations are updated to build these dicts.
- Output folder structure and `.txt` transcript format are unchanged, so existing scraped transcripts and resume state remain compatible.
