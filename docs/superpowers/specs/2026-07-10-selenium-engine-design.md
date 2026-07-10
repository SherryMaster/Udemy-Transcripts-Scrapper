# Selenium Engine Overhaul — Design Spec

**Date:** 2026-07-10
**Branch:** `feature/selenium-engine` (from `main`)
**Status:** Approved by user

## Goal

Replace the browser-use subprocess bridge in `scraper.py` with a persistent
`undetected-chromedriver` instance. Preserve the proven in-page JavaScript
(curriculum discovery, batch transcript fetch, VTT parsing). Simplify the
session threading model. Harden error handling. Keep the frontend ↔ backend
contract unchanged.

## Motivation

`browser-use` is a CLI for ad-hoc agent interaction, not a library built for
scripted automation at scale. The current scraper shells out to it via
`subprocess.run(["bash","-c", ...])` for every JS call — fragile, slow to
spin up, and not designed for high-throughput batch work. Selenium is the
standard for scripted browser automation; `undetected-chromedriver` wraps
Selenium with anti-bot-detection patches so Udemy's Cloudflare layer does
not block the automated Chrome.

## Verified Facts

All findings confirmed via browser-use exploration against the user's
logged-in Udemy session (Chrome 148.0.7778.215, course id 6950981
"Recreate Stardew Valley in Godot", 6 sections / 32 lectures).

- **Course info endpoint** — `GET /api-2.0/courses/{slug}/?fields[course]=id,title` returns `{id, title}`. Works.
- **Curriculum endpoint** — `POST /api/2024-01/graphql/` with the existing GraphQL query returns sections → items (Lecture/Quiz). Works.
- **Transcript endpoint** — `GET /api-2.0/users/me/subscribed-courses/{courseId}/lectures/{lectureId}/?fields[lecture]=asset&fields[asset]=captions` returns caption list. Works.
- **VTT download** — `GET {caption.url}` returns valid WebVTT. Works. Captions observed as `source:"auto"` (auto-generated); the existing `en_` locale fallback handles this.
- **Auth** — All of the above succeed only when the browser session is logged in. Auth is session-cookie based; no API tokens.

### undetected-chromedriver (v3.5.5, latest)

- Pins `selenium>=4.9`.
- Persistent profile via `options.user_data_dir = "<path>"`. Directory is auto-created if missing and is **not** auto-removed on exit — ideal for a "log in once" profile.
- `version_main=<int>` pins the chromedriver build to the installed Chrome major version (148 here).
- `use_subprocess=True` is the default; keep it so the driver survives Python's main-thread quirks.
- `uc.Chrome(options=options, version_main=148)` is the full constructor call.

### Critical Selenium adaptation

`driver.execute_script` **cannot** return a resolved Promise — it returns
synchronously and a Promise result would be `{}`. The existing scraper's
async-IIFE-returning-`JSON.stringify(result)` pattern works under
browser-use's `js()` helper but fails under Selenium. We must use
`driver.execute_async_script` with a completion callback:

```js
var cb = arguments[arguments.length - 1];
(async () => {
  // ...existing JS body...
  cb(JSON.stringify(result));
})().catch(e => cb(JSON.stringify({error: e.message})));
```

`driver.set_script_timeout(timeout_seconds)` must be set before each call
(default 60s for batch fetches, 30s for discovery).

## Architecture

```
app.py / frontend/*  →  backend/server.py (FastAPI + WS, unchanged contract)
                          ↓
                    backend/scraper_session.py  (refactored: single-driver model)
                          ↓
                    scraper.py  (UdemyScraper)  ← ENGINE REBUILT on undetected-chromedriver
                          ↓
                    driver.py  (SeleniumDriverManager)  ← NEW
                          ↓
              undetected-chromedriver (persistent profile, execute_async_script)
                          ↓
                    Udemy APIs (auth via logged-in session cookies)
                          ↓
              progress_tracker.py (unchanged)
```

Layering is identical to today; only engine + session + error-handling
internals change.

## Component: `driver.py` (new)

Single-responsibility module managing the Chrome lifecycle. ~80 lines.

```
class SeleniumDriverManager:
    PROFILE_DIR = "~/.udemy-scraper-profile"   (configurable via UDEMY_SCRAPER_PROFILE env var)
    __init__(profile_dir=None, version_main=148)
    connect() -> uc.Chrome          (lazy; creates driver on first call, reuses after)
    is_logged_in() -> bool          (navigates to udemy, checks for login wall)
    ensure_logged_in() -> None     (raises RuntimeError with clear message if not logged in)
    execute_async_js(js_body, timeout=60) -> str
                                    (wraps execute_async_script + callback shim)
    reconnect() -> uc.Chrome       (quit + relaunch on same profile; one-shot guard)
    quit()
```

- `connect()` launches `uc.Chrome(options)` with `options.user_data_dir = PROFILE_DIR`. First-ever launch = fresh profile.
- `execute_async_js(js_body, timeout)` wraps the user's JS in the callback shim (see "Critical Selenium adaptation" above), calls `driver.set_script_timeout(timeout)`, then `driver.execute_async_script(wrapped)`. Returns the raw string. One chokepoint for all JS execution.
- `reconnect()` quits the dead driver and relaunches `uc.Chrome` on the same persistent profile. A `_reconnecting` flag prevents infinite reconnect loops (one reconnect attempt per failure).

## Component: `scraper.py` (rebuilt)

`UdemyScraper` keeps its public method names so `scraper_session.py` and
existing tests stay source-compatible.

### Public surface (unchanged signatures)

- `__init__(log_callback=None)`
- `connect_and_navigate(url) -> bool`
- `discover_course() -> dict`
- `create_folder_structure(base_dir) -> str`
- `fetch_transcripts_batch(lecture_ids, retries=2) -> dict`
- `save_transcript(section, lecture, lecture_index, transcript)`
- `scrape_parallel(...)` — kept as the entry name but internally sequential (see Section 3)

### Internal changes

- `run_browser_use()` — **deleted**.
- `_js()` / `_js_json()` — reimplemented on top of `SeleniumDriverManager.execute_async_js()`. `_js_json` still JSON-parses the returned string.
- `connect_and_navigate(url)` — `self.driver = DriverManager.connect()`, `driver.get(course_url)`, `DriverManager.ensure_logged_in()`. No more `new_tab()` / `run_browser_use`.
- `discover_course()` — calls `_js_json(code)` which now routes through `execute_async_js`. The curriculum GraphQL + course-info JS stays **byte-for-byte the same** — it already returns `JSON.stringify(...)`.
- `fetch_transcripts_batch()` — the big `Promise.all` fetch-loop JS stays the same; executed via `execute_async_js`. Split-batch retry fallback is reworked (see Section 4).

## Component: `backend/scraper_session.py` (refactored)

The current session spawns `num_threads` worker threads, each creating its own
`UdemyScraper` and pulling batches from a `queue.Queue`. With one persistent
driver this multi-threaded model is dead weight — `execute_async_script`
calls serialize on a single driver anyway.

### Refactored `_run()`

- No `queue.Queue`, no worker threads, no `num_threads` workers.
- Single sequential loop: discover → create folders → loop over lectures in
  chunks of `batch_size` → `fetch_transcripts_batch(chunk)` → process results
  → emit events → check `stop_flag` between batches.
- `scrape_parallel()` is the kept entry name but internally runs sequentially.
- `num_threads` param is kept in the API (`server.py`, frontend) for backward
  compatibility but ignored — a log line notes it's deprecated. No frontend
  change needed.

### `retry_failed()`

Stays: pulls failed lecture IDs, calls `fetch_transcripts_batch` on the same
driver, emits results. Already single-threaded — minimal change.

### `events()` / WebSocket stream

Unchanged. The queue-based event emitter (`_tqueue`) stays exactly as-is; the
scrape loop pushes events into it the same way.

## Error handling

Three failure categories, each with distinct handling.

### 1. Auth failures (login wall)

- `connect_and_navigate` navigates to the course URL, then checks for Udemy's
  login redirect / "Sign in" button via `execute_script` DOM check.
- If not logged in: emit
  `{"type":"error","message":"Not logged in. Launch the scraper, log into Udemy once in the Selenium window, then retry."}`
  and stop. No silent retry loop.
- Documented: the first-ever run requires a manual login in the visible Chrome window.

### 2. `execute_async_script` timeout

- `driver.set_script_timeout(timeout)` set per call (default 60s for batch
  fetches, 30s for discovery).
- On `TimeoutException`: the whole batch is marked `error` for all its lecture
  IDs. The split-batch fallback (below) kicks in.
- On `JavascriptException` / `WebDriverException`: same treatment — log + mark
  batch as error, trigger fallback.

### 3. Split-batch fallback (rewritten)

Current logic: on any exception with `retries > 0` and `len > 1`, split the
batch in half and recurse. Kept but clarified:

```python
def fetch_transcripts_batch(self, lecture_ids, retries=2):
    try:
        raw = self.driver.execute_async_js(BATCH_JS, timeout=60)
        return json.loads(raw)
    except (TimeoutException, JavascriptException, WebDriverException):
        if retries > 0 and len(lecture_ids) > 1:
            mid = len(lecture_ids) // 2
            left = self.fetch_transcripts_batch(lecture_ids[:mid], retries - 1)
            right = self.fetch_transcripts_batch(lecture_ids[mid:], retries - 1)
            left.update(right)
            return left
        # Base case: single lecture failed, or out of retries
        return {lid: {"s": "error"} for lid in lecture_ids}
```

- Per-lecture API errors (`api_error`, `no_captions`, `no_english`,
  `vtt_error`, `empty`) are **not** exceptions — they are returned in the
  result dict by the in-page JS and handled as before (skipped vs. failed).
  Only driver-level failures trigger the split.

### 4. Stale driver detection

- If `execute_async_js` raises `InvalidSessionIdException` or the driver
  process dies, `DriverManager.reconnect()` quits + relaunches `uc.Chrome`
  on the same persistent profile and retries the batch once.
- A `_reconnecting` flag prevents infinite reconnect loops.

## Profile & login flow

### Persistent profile directory

`~/.udemy-scraper-profile` (configurable via env var `UDEMY_SCRAPER_PROFILE`).

### First run (setup)

1. `DriverManager.connect()` launches `uc.Chrome(user_data_dir=PROFILE_DIR,
   version_main=148)`. Fresh profile, visible window.
2. Scraper navigates to `https://www.udemy.com/join/login`.
3. Detects login wall → emits error event telling user to log in. The driver
   is **kept alive** (not quit) so the visible window remains open for the
   user to log into. The session ends but `DriverManager` holds the driver.
4. User logs in manually in the visible Chrome window. Cookies saved to
   PROFILE_DIR.
5. User clicks "Start" again. `ScraperSession._run()` reuses the existing
   driver (if alive) or reconnects on the same profile (cookies now present)
   and the session proceeds.
6. On `quit()`, profile persists on disk.

To keep the driver alive across sessions on the login-wall path,
`SeleniumDriverManager` is held at module scope (a single shared instance)
rather than recreated per `ScraperSession`. `connect()` returns the existing
driver if present, otherwise launches a new one.

### Subsequent runs

1. `DriverManager.connect()` launches with the existing PROFILE_DIR. Cookies
   load → already logged in.
2. `ensure_logged_in()` confirms by checking the DOM after navigation. If
   session expired (rare), re-emit the login error.

### Driver lifecycle

- `SeleniumDriverManager` is a single shared instance held at module scope
  in `driver.py`, so it survives across `ScraperSession` instances (needed for
  the first-run login flow).
- `ScraperSession._run()` calls `SeleniumDriverManager.connect()` which
  returns the existing driver if alive, or launches a new one.
- Driver is created lazily on first `connect_and_navigate` call.
- On `stop()`: session sets `stop_flag`; the scrape loop checks it between
  batches, then `driver.quit()` in a `finally` block. The shared manager
  drops its reference so the next `connect()` launches fresh.
- On the login-wall path: the session ends **without** calling `quit()`, so
  the visible window stays open for the user to log in. The next "Start"
  reuses the live driver.
- On app shutdown (`shutdown_session()`): any live driver is quit.

## Testing

### Existing tests

- **`test_extract_slug.py`** — unchanged (pure regex, no browser dep).
- **`test_progress_tracker.py`** — unchanged (pure file I/O).
- **`test_scraper_session.py`** — update: mock `DriverManager` instead of
  `run_browser_use`. Inject a fake driver that returns canned
  `execute_async_js` results. Tests for: sequential batch loop, `stop_flag`
  between batches, `retry_failed` path, login-wall detection.
- **`test_server.py`** — unchanged (FastAPI contract stays the same).

### New tests

- **`test_driver_manager.py`** — unit tests for `execute_async_js` callback
  shim (verify it wraps JS correctly, handles errors, parses JSON), profile
  dir resolution, `version_main` detection. No real Chrome launched in unit
  tests (mock `uc.Chrome`).

### Integration test (manual, not CI)

Run against the 32-lecture "Recreate Stardew Valley in Godot" course
end-to-end, verify all transcripts saved to disk.

## Dependencies

`requirements.txt` additions:

```
undetected-chromedriver>=3.5.5
selenium>=4.9
```

Remove the browser-use comment line.

## Files changed

| File | Change |
|---|---|
| `scraper.py` | Engine rebuilt (keep public method names) |
| `driver.py` | New, ~80 lines |
| `backend/scraper_session.py` | Threading refactor (single-driver sequential) |
| `requirements.txt` | Add selenium/uc, remove browser-use note |
| `tests/test_scraper_session.py` | Update mocks |
| `tests/test_driver_manager.py` | New |

## Files unchanged

`app.py`, `backend/server.py`, `frontend/*`, `progress_tracker.py`,
`tests/test_extract_slug.py`, `tests/test_progress_tracker.py`,
`tests/test_server.py`.

## Migration risk

Low. Frontend ↔ backend contract (REST + WS events) is untouched. The only
behavioral change is the first-run login requirement, which is a UX step, not
a code interface change. The `main` branch is unaffected (work on
`feature/selenium-engine`).
