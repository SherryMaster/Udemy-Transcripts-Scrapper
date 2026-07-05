# UI Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the CustomTkinter GUI of the Udemy Transcript Scraper with a polished PyWebView + FastAPI + WebSocket web UI (Midnight Pro theme) that fixes the parallel-progress glitch via a per-lecture status-box grid.

**Architecture:** A FastAPI server serves a vanilla-JS frontend and exposes a WebSocket; the existing multi-threaded scraper emits per-lecture event dicts that a thin `ScraperSession` adapter normalizes and pushes over the WS; the JS frontend applies each event to one independent cell (no shared counter → no race). `scraper.py` and `progress_tracker.py` stay almost unchanged.

**Tech Stack:** Python 3, FastAPI, uvicorn, websockets, PyWebView; vanilla HTML/CSS/JS frontend; pytest.

**Spec:** `docs/superpowers/specs/2026-07-05-ui-overhaul-design.md`

---

## File Structure

```
app.py                      # REWRITE: PyWebView launcher + uvicorn thread + js_api (browse)
backend/
  __init__.py               # empty (package marker)
  server.py                 # NEW: FastAPI app, HTTP endpoints, /ws handler
  scraper_session.py        # NEW: scraper ↔ WS adapter (normalizes statuses, emits events)
scraper.py                  # MODIFY: progress_callback → event dict; track skipped; final signal
progress_tracker.py         # UNCHANGED (add tests only)
frontend/
  index.html                # NEW: two-pane layout markup
  style.css                 # NEW: Midnight Pro theme
  app.js                    # NEW: ScraperUI module (state, render, WS, commands)
  dev-harness.html          # NEW: canned-event replay for visual verification
tests/
  __init__.py               # empty
  test_extract_slug.py      # NEW: pure helper tests
  test_progress_tracker.py  # NEW: tracker characterization tests
  test_scraper_events.py    # NEW: scraper emits correct event dicts
  test_scraper_session.py   # NEW: status normalization + event building
  test_server.py            # NEW: HTTP endpoints + WS feed
requirements.txt            # NEW
run.sh                      # MODIFY: keep as-is (already works)
```

**Responsibility boundaries:**
- `scraper.py` — scraping engine only; emits native-status event dicts; knows nothing of the web/UI.
- `backend/scraper_session.py` — the *only* glue: orchestrates the scraper's lifecycle, normalizes statuses to the box-state vocabulary, pushes events to an asyncio queue. Knows nothing of HTTP/WS.
- `backend/server.py` — HTTP/WS transport only; delegates to a ScraperSession; no scraping logic.
- `frontend/app.js` — holds the state model; applies events to cells; re-renders incrementally. Pure module exposing `ScraperUI`.

---

## Task 1: Project setup — dependencies & package skeleton

**Files:**
- Create: `requirements.txt`
- Create: `backend/__init__.py`, `tests/__init__.py`

- [ ] **Step 1: Write `requirements.txt`**

```
fastapi>=0.110
uvicorn[standard]>=0.27
pywebview>=5.0
pytest>=8.0
httpx>=0.27
# browser-use is invoked via subprocess by scraper.py; install it separately per its docs.
```

- [ ] **Step 2: Create empty package markers**

`backend/__init__.py` and `tests/__init__.py` — each an empty file.

- [ ] **Step 3: Install dependencies**

Run: `source venv/bin/activate && pip install -r requirements.txt`
Expected: all packages install successfully.

- [ ] **Step 4: Commit**

```bash
git add requirements.txt backend/__init__.py tests/__init__.py
git commit -m "chore: add web-ui dependencies and package skeleton"
```

---

## Task 2: Pure helper tests — `extract_course_slug` & `sanitize_filename`

These functions already exist in `scraper.py` (lines 27–45). We lock their behavior with characterization tests before touching the file.

**Files:**
- Test: `tests/test_extract_slug.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest
from scraper import extract_course_slug, sanitize_filename


@pytest.mark.parametrize("url,expected", [
    ("https://www.udemy.com/course/react-the-complete-guide/learn", "react-the-complete-guide"),
    ("https://www.udemy.com/course/python-ds/overview", "python-ds"),
    ("https://www.udemy.com/course/my-course/?ref=menu", "my-course"),
])
def test_extract_course_slug(url, expected):
    assert extract_course_slug(url) == expected


def test_extract_course_slug_invalid():
    with pytest.raises(ValueError):
        extract_course_slug("https://example.com/no/course/here")


def test_sanitize_filename_strips_dangerous_chars():
    assert sanitize_filename('hello/world:file*.txt') == "hello_world_file_.txt"


def test_sanitize_filename_truncates_long_names():
    assert sanitize_filename("x" * 300) == "x" * 200


def test_sanitize_filename_empty_returns_untitled():
    assert sanitize_filename("   ") == "untitled"
```

- [ ] **Step 2: Run test to verify it passes (functions already exist)**

Run: `source venv/bin/activate && pytest tests/test_extract_slug.py -v`
Expected: PASS (5 tests).

- [ ] **Step 3: Commit**

```bash
git add tests/test_extract_slug.py
git commit -m "test: characterize extract_course_slug and sanitize_filename"
```

---

## Task 3: ProgressTracker characterization tests

`progress_tracker.py` is unchanged, but we add tests to lock resume/corruption behavior that the new UI relies on.

**Files:**
- Test: `tests/test_progress_tracker.py`

- [ ] **Step 1: Write the failing test**

```python
import json
import os
import time
from pathlib import Path

from progress_tracker import ProgressTracker


def test_init_new_course(tmp_path):
    t = ProgressTracker(str(tmp_path))
    t.init_course("my-course", 123, "My Course", 4, 40, str(tmp_path))
    assert t.state["course_slug"] == "my-course"
    assert t.state["total_lectures"] == 40
    assert t.completed_count == 0
    assert os.path.exists(t.state_path)


def test_init_same_course_does_not_reset(tmp_path):
    t = ProgressTracker(str(tmp_path))
    t.init_course("my-course", 123, "My Course", 4, 40, str(tmp_path))
    t.mark_lecture_done("lec-1", 0, 0)
    t.init_course("my-course", 123, "My Course", 4, 40, str(tmp_path))  # resume
    assert t.is_lecture_done("lec-1") is True


def test_mark_done_and_failed(tmp_path):
    t = ProgressTracker(str(tmp_path))
    t.init_course("c", 1, "C", 1, 3, str(tmp_path))
    t.mark_lecture_done("a", 0, 0)
    t.mark_lecture_failed("b", "no_captions")
    assert t.completed_count == 1
    assert t.failed_count == 1
    assert t.is_lecture_done("a") is True


def test_is_resumable(tmp_path):
    t = ProgressTracker(str(tmp_path))
    assert t.is_resumable is False
    t.init_course("c", 1, "C", 1, 5, str(tmp_path))
    assert t.is_resumable is True
    for i, lid in enumerate(["l1", "l2", "l3", "l4", "l5"]):
        t.mark_lecture_done(lid, 0, i)
    assert t.is_resumable is False  # all done


def test_corrupted_state_file_falls_back_to_fresh(tmp_path):
    state_path = Path(tmp_path) / "scrape_state.json"
    state_path.write_text("{ not valid json ")
    t = ProgressTracker(str(tmp_path))
    assert t.state["course_slug"] is None
    assert t.state["total_lectures"] == 0


def test_resume_info(tmp_path):
    t = ProgressTracker(str(tmp_path))
    t.init_course("c", 1, "C", 2, 10, str(tmp_path))
    t.mark_lecture_done("l1", 0, 0)
    info = t.get_resume_info()
    assert info["course_slug"] == "c"
    assert info["completed"] == 1
    assert info["total"] == 10
```

- [ ] **Step 2: Run test to verify it passes**

Run: `source venv/bin/activate && pytest tests/test_progress_tracker.py -v`
Expected: PASS (6 tests).

- [ ] **Step 3: Commit**

```bash
git add tests/test_progress_tracker.py
git commit -m "test: characterize ProgressTracker resume and corruption handling"
```

---

## Task 4: Scraper callback contract — emit event dicts

Change `scraper.py`'s `scrape_parallel` so the `progress_callback` receives a single event **dict** with the scraper's native status (`working`/`saved`/`skipped`/`failed`), plus a final `scrape_finished` signal. The session layer (Task 5) normalizes these to box-state vocabulary. Batching, retries, and discovery are untouched.

**Files:**
- Modify: `scraper.py` (the `worker` function inside `scrape_parallel`, ~lines 267–330, and the final signal ~line 345)
- Test: `tests/test_scraper_events.py`

- [ ] **Step 1: Write the failing test**

```python
import json
from unittest.mock import patch

from scraper import UdemyScraper


def _make_scraper_with_sections():
    s = UdemyScraper()
    s.course_id = 999
    s.output_dir = "/tmp/test-out"
    s.sections = [
        {"index": 1, "id": "sec1", "title": "Intro", "folder_name": "01_Intro",
         "lectures": [
             {"id": "l1", "title": "Welcome"},
             {"id": "l2", "title": "Quiz 1"},
             {"id": "l3", "title": "Setup"},
         ]},
    ]
    return s


def test_scraper_emits_event_dicts_per_status():
    s = _make_scraper_with_sections()
    events = []

    fake_results = json.dumps({
        "l1": {"s": "ok", "t": "hello world"},
        "l2": {"s": "api_error"},          # quiz -> skipped
        "l3": {"s": "no_captions"},         # -> skipped
    })

    with patch("scraper.run_browser_use", return_value=fake_results), \
         patch("scraper.UdemyScraper.save_transcript"):
        s.scrape_parallel(
            base_dir="/tmp/test-out",
            progress_callback=events.append,
            stop_check=lambda: False,
            batch_size=5,
            num_threads=1,
            skip_discovery=True,
        )

    lecture_events = [e for e in events if e.get("type") == "lecture_status"]
    statuses = {(e["sectionIdx"], e["lectureIdx"]): e["status"] for e in lecture_events}
    assert statuses[(0, 0)] == "saved"        # l1 ok
    assert statuses[(0, 1)] == "skipped"      # l2 api_error
    assert statuses[(0, 2)] == "skipped"      # l3 no_captions

    working = [e for e in lecture_events if e["status"] == "working"]
    assert len(working) == 3                   # one working event per lecture in batch

    assert any(e.get("type") == "scrape_finished" for e in events)


def test_scraper_failed_event_on_error_status():
    s = _make_scraper_with_sections()
    events = []
    fake_results = json.dumps({"l1": {"s": "vtt_error"}, "l2": {"s": "error", "m": "boom"}, "l3": {"s": "empty"}})
    with patch("scraper.run_browser_use", return_value=fake_results), \
         patch("scraper.UdemyScraper.save_transcript"):
        s.scrape_parallel("/tmp/test-out", events.append, lambda: False, 5, 1, skip_discovery=True)
    fails = [e for e in events if e.get("type") == "lecture_status" and e["status"] == "failed"]
    assert len(fails) == 3


def test_scraper_stop_check_aborts():
    s = _make_scraper_with_sections()
    events = []
    fake_results = json.dumps({"l1": {"s": "ok", "t": "hi"}})
    stop_after = [0]

    def stop_check():
        stop_after[0] += 1
        return stop_after[0] > 1   # stop after first check passes

    with patch("scraper.run_browser_use", return_value=fake_results), \
         patch("scraper.UdemyScraper.save_transcript"):
        s.scrape_parallel("/tmp/test-out", events.append, stop_check, 1, 1, skip_discovery=True)

    saved = [e for e in events if e.get("type") == "lecture_status" and e["status"] == "saved"]
    assert len(saved) <= 1   # did not process all 3 lectures
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source venv/bin/activate && pytest tests/test_scraper_events.py -v`
Expected: FAIL — `events` elements are tuples/strings (old contract), not dicts; `e.get` raises `AttributeError` or statuses don't match.

- [ ] **Step 3: Modify `scraper.py` worker to emit event dicts**

In `scrape_parallel`, replace the three `progress_callback(...)` "working"/"saved"/"skipped"/"failed" call sites and the final signal. Add a small `emit` helper inside `worker`. The changes are localized to the `worker` inner function and the final line.

Replace the worker's "Report working" block:
```python
                # Report working
                if progress_callback:
                    for si, li, lec in batch:
                        progress_callback({
                            "type": "lecture_status",
                            "sectionIdx": si, "lectureIdx": li,
                            "status": "working",
                            "message": f"[W{worker_id}] {lec['title'][:40]}",
                        })
```

Replace the results-processing block (the `with lock:` section) with:
```python
                with lock:
                    for si, li, lec in batch:
                        result = results.get(lec["id"], {"s": "error"})
                        status = result.get("s", "error")
                        transcript = result.get("t", "")
                        size = None

                        if status == "ok" and transcript:
                            self.save_transcript(self.sections[si], lec, li + 1, transcript)
                            completed[0] += 1
                            size = len(transcript)
                            self.log(f"  [W{worker_id}] Saved: {lec['title'][:50]} ({len(transcript)} chars)")
                            if progress_callback:
                                progress_callback({
                                    "type": "lecture_status", "sectionIdx": si, "lectureIdx": li,
                                    "status": "saved", "message": f"Saved: {lec['title'][:50]}",
                                    "size": size,
                                })
                        elif status in ("no_captions", "no_english", "api_error"):
                            completed[0] += 1
                            if progress_callback:
                                progress_callback({
                                    "type": "lecture_status", "sectionIdx": si, "lectureIdx": li,
                                    "status": "skipped", "message": f"Skipped ({status})",
                                })
                        else:
                            failed[0] += 1
                            self.log(f"  [W{worker_id}] Failed ({status}): {lec['title'][:50]}")
                            if progress_callback:
                                progress_callback({
                                    "type": "lecture_status", "sectionIdx": si, "lectureIdx": li,
                                    "status": "failed", "message": f"Failed ({status})",
                                })
```

Replace the final `if progress_callback:` line at the end of `scrape_parallel`:
```python
        if progress_callback:
            progress_callback({"type": "scrape_finished", "completed": completed[0], "failed": failed[0]})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source venv/bin/activate && pytest tests/test_scraper_events.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add scraper.py tests/test_scraper_events.py
git commit -m "feat(scraper): emit per-lecture event dicts instead of positional callback"
```

---

## Task 5: ScraperSession adapter — status normalization & event orchestration

The session orchestrates the scraper lifecycle and pushes normalized events to an asyncio queue (thread-safe). It is the only glue between `scraper.py` and `server.py`.

**Files:**
- Create: `backend/scraper_session.py`
- Test: `tests/test_scraper_session.py`

- [ ] **Step 1: Write the failing test**

```python
import asyncio
from backend.scraper_session import normalize_status, ScraperSession


def test_normalize_status_mapping():
    assert normalize_status("working") == "in-progress"
    assert normalize_status("saved") == "success"
    assert normalize_status("skipped") == "skipped"
    assert normalize_status("failed") == "failed"
    assert normalize_status("unknown") == "failed"   # safe default


def test_session_wraps_callback_and_normalizes():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    session = ScraperSession(loop=loop)
    session.start_workers = lambda *a, **kw: None   # don't really scrape

    received = []
    loop.call_soon_threadsafe = lambda f, *a: f(*a)  # make it synchronous for test
    session._emit({"type": "lecture_status", "sectionIdx": 0, "lectureIdx": 2,
                   "status": "saved", "message": "Saved: X", "size": 99})

    async def drain():
        while session.queue.empty():
            await asyncio.sleep(0)
        return session.queue.get_nowait()

    ev = loop.run_until_complete(drain())
    assert ev["type"] == "lecture_status"
    assert ev["status"] == "success"     # normalized
    assert ev["size"] == 99
    loop.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source venv/bin/activate && pytest tests/test_scraper_session.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.scraper_session'`.

- [ ] **Step 3: Implement `backend/scraper_session.py`**

```python
import asyncio
import threading
import time

from scraper import UdemyScraper
from progress_tracker import ProgressTracker


NATIVE_TO_BOX = {
    "working": "in-progress",
    "saved": "success",
    "skipped": "skipped",
    "failed": "failed",
}


def normalize_status(native: str) -> str:
    return NATIVE_TO_BOX.get(native, "failed")


class ScraperSession:
    def __init__(self, loop=None):
        self.loop = loop or asyncio.new_event_loop()
        self.queue = asyncio.Queue()
        self.scraper = None
        self.tracker = None
        self.thread = None
        self.stop_flag = False
        self.is_running = False
        self.started_at = None
        self.course_snapshot = None
        self.lecture_states = {}   # (sectionIdx, lectureIdx) -> box-state

    def _emit(self, event: dict):
        if self.loop.is_closed():
            return
        try:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, event)
        except RuntimeError:
            pass

    def _on_scraper_event(self, event: dict):
        if event.get("type") == "lecture_status":
            key = (event["sectionIdx"], event["lectureIdx"])
            normalized = normalize_status(event["status"])
            self.lecture_states[key] = normalized
            self._emit({
                "type": "lecture_status",
                "sectionIdx": event["sectionIdx"],
                "lectureIdx": event["lectureIdx"],
                "status": normalized,
                "message": event.get("message", ""),
                "size": event.get("size"),
            })
            self._emit_progress()
        elif event.get("type") == "scrape_finished":
            self._emit({
                "type": "done",
                **self._counts(),
            })

    def _on_log(self, message: str):
        level = "info"
        if "Saved" in message:
            level = "success"
        elif "Failed" in message or "error" in message.lower():
            level = "error"
        elif "Skipped" in message or "No captions" in message:
            level = "warn"
        self._emit({"type": "log", "message": message, "level": level})

    def _counts(self) -> dict:
        completed = self.tracker.completed_count if self.tracker else 0
        failed = self.tracker.failed_count if self.tracker else 0
        total = self.tracker.state.get("total_lectures", 0) if self.tracker else 0
        states = list(self.lecture_states.values())
        active = states.count("in-progress")
        skipped = states.count("skipped")
        success = states.count("success")
        elapsed_ms = int((time.time() - self.started_at) * 1000) if self.started_at else 0
        return {
            "completed": completed, "total": total, "failed": failed,
            "skipped": skipped, "active": active, "success": success,
            "elapsedMs": elapsed_ms,
        }

    def _emit_progress(self):
        self._emit({"type": "progress", **self._counts()})

    def _run(self, url: str, output_dir: str, batch_size: int, num_threads: int, resume: bool):
        try:
            self.scraper = UdemyScraper(log_callback=self._on_log)
            self.tracker = ProgressTracker(output_dir)

            self._emit({"type": "log", "message": "Connecting to browser...", "level": "info"})
            self.scraper.connect_and_navigate(url)

            self._emit({"type": "log", "message": "Discovering course structure...", "level": "info"})
            self.scraper.discover_course()

            self.tracker.init_course(
                self.scraper.course_slug, self.scraper.course_id, self.scraper.course_title,
                len(self.scraper.sections),
                sum(len(s["lectures"]) for s in self.scraper.sections),
                output_dir,
            )
            self.scraper.create_folder_structure(output_dir)

            self.course_snapshot = {
                "courseTitle": self.scraper.course_title,
                "sections": [
                    {"index": s["index"], "title": s["title"],
                     "lectures": [{"index": li, "id": l["id"], "title": l["title"]}
                                  for li, l in enumerate(s["lectures"])]}
                    for s in self.scraper.sections
                ],
            }
            self._emit({"type": "course_discovered", **self.course_snapshot})

            if resume:
                for si, section in enumerate(self.scraper.sections):
                    for li, lec in enumerate(section["lectures"]):
                        if self.tracker.is_lecture_done(lec["id"]):
                            self.lecture_states[(si, li)] = "success"
                            self._emit({"type": "lecture_status", "sectionIdx": si,
                                        "lectureIdx": li, "status": "success",
                                        "message": "Resumed", "size": None})
            self._emit_progress()

            self.scraper.scrape_parallel(
                base_dir=output_dir,
                progress_callback=self._on_scraper_event,
                stop_check=lambda: self.stop_flag,
                batch_size=batch_size,
                num_threads=num_threads,
                skip_discovery=True,
            )
            self._emit_progress()
        except Exception as e:
            self._emit({"type": "error", "message": str(e)})
        finally:
            self.is_running = False
            self.stop_flag = False

    def start(self, url: str, output_dir: str, batch_size: int, num_threads: int, resume: bool = False):
        self.stop_flag = False
        self.is_running = True
        self.started_at = time.time()
        self.thread = threading.Thread(
            target=self._run,
            args=(url, output_dir, batch_size, num_threads, resume),
            daemon=True,
        )
        self.thread.start()

    def stop(self):
        self.stop_flag = True

    def retry_failed(self):
        success = []
        for (si, li), st in list(self.lecture_states.items()):
            if st == "failed":
                success.append((si, li))
                self.lecture_states[(si, li)] = "in-progress"
                self._emit({"type": "lecture_status", "sectionIdx": si, "lectureIdx": li,
                            "status": "in-progress", "message": "Retrying", "size": None})
        self._emit_progress()
        return success

    async def events(self):
        if self.course_snapshot is not None:
            yield {"type": "course_discovered", **self.course_snapshot}
            for (si, li), st in self.lecture_states.items():
                yield {"type": "lecture_status", "sectionIdx": si, "lectureIdx": li,
                       "status": st, "message": "", "size": None}
            yield {"type": "progress", **self._counts()}
        while True:
            event = await self.queue.get()
            yield event
            if event.get("type") in ("done", "error"):
                break
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source venv/bin/activate && pytest tests/test_scraper_session.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/scraper_session.py tests/test_scraper_session.py
git commit -m "feat(backend): ScraperSession adapter normalizes statuses and emits WS events"
```

---

## Task 6: FastAPI server — HTTP endpoints & WebSocket

**Files:**
- Create: `backend/server.py`
- Test: `tests/test_server.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest
from fastapi.testclient import TestClient

import backend.server as server_mod
from backend.server import app, set_session_factory


class FakeSession:
    def __init__(self, **kw):
        self.started = False
        self.stopped = False
        self.retried = False

    def start(self, url, output_dir, batch_size, num_threads, resume=False):
        self.started = True

    def stop(self):
        self.stopped = True

    def retry_failed(self):
        self.retried = True
        return []

    async def events(self):
        yield {"type": "course_discovered", "courseTitle": "Fake",
               "sections": [{"index": 1, "title": "S1", "lectures": [{"index": 0, "id": "l1", "title": "L1"}]}]}
        yield {"type": "lecture_status", "sectionIdx": 0, "lectureIdx": 0,
               "status": "success", "message": "Saved", "size": 10}
        yield {"type": "progress", "completed": 1, "total": 1, "failed": 0,
               "skipped": 0, "active": 0, "success": 1, "elapsedMs": 0}
        yield {"type": "done", "completed": 1, "total": 1, "failed": 0,
               "skipped": 0, "active": 0, "success": 1, "elapsedMs": 0}


@pytest.fixture(autouse=True)
def fake_factory():
    set_session_factory(lambda **kw: FakeSession())
    yield
    set_session_factory(None)


def test_start_returns_202():
    client = TestClient(app)
    r = client.post("/api/start", json={"url": "https://www.udemy.com/course/x/learn",
                                        "outputDir": "/tmp/out", "batchSize": 5, "numThreads": 3})
    assert r.status_code == 202


def test_start_validates_url():
    client = TestClient(app)
    r = client.post("/api/start", json={"url": "not-a-url", "outputDir": "/tmp/out",
                                        "batchSize": 5, "numThreads": 3})
    assert r.status_code == 400
    assert "url" in r.json()["error"].lower()


def test_stop_and_retry():
    client = TestClient(app)
    client.post("/api/start", json={"url": "https://www.udemy.com/course/x/learn",
                                    "outputDir": "/tmp/out", "batchSize": 5, "numThreads": 3})
    assert client.post("/api/stop").status_code == 202
    assert client.post("/api/retry-failed").status_code == 202


def test_ws_feeds_events_in_order():
    client = TestClient(app)
    client.post("/api/start", json={"url": "https://www.udemy.com/course/x/learn",
                                    "outputDir": "/tmp/out", "batchSize": 5, "numThreads": 3})
    with client.websocket_connect("/ws") as ws:
        ev = ws.receive_json()
        assert ev["type"] == "course_discovered"
        ev = ws.receive_json()
        assert ev["type"] == "lecture_status" and ev["status"] == "success"
        ev = ws.receive_json()
        assert ev["type"] == "progress"
        ev = ws.receive_json()
        assert ev["type"] == "done"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source venv/bin/activate && pytest tests/test_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.server'`.

- [ ] **Step 3: Implement `backend/server.py`**

```python
import re

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from backend.scraper_session import ScraperSession

app = FastAPI(title="Udemy Scraper")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

_session_factory = None
app.state.session = None


def set_session_factory(factory):
    global _session_factory
    _session_factory = factory


def _make_session(**kw):
    if _session_factory is not None:
        return _session_factory(**kw)
    return ScraperSession(**kw)


URL_RE = re.compile(r"https?://www\.udemy\.com/course/[^/]+")


@app.get("/")
async def index():
    return JSONResponse({"status": "ok"})


@app.post("/api/start")
async def api_start(payload: dict):
    url = (payload.get("url") or "").strip()
    if not URL_RE.match(url):
        return JSONResponse({"error": "Invalid Udemy course URL"}, status_code=400)
    output_dir = (payload.get("outputDir") or "").strip()
    if not output_dir:
        return JSONResponse({"error": "outputDir is required"}, status_code=400)
    batch_size = int(payload.get("batchSize", 5))
    num_threads = int(payload.get("numThreads", 3))

    app.state.session = _make_session()
    app.state.session.start(url, output_dir, batch_size, num_threads, resume=False)
    return JSONResponse({"status": "started"}, status_code=202)


@app.post("/api/resume")
async def api_resume(payload: dict):
    output_dir = (payload.get("outputDir") or "").strip()
    if not output_dir:
        return JSONResponse({"error": "outputDir is required"}, status_code=400)
    app.state.session = _make_session()
    app.state.session.start("", output_dir, 5, 3, resume=True)
    return JSONResponse({"status": "resumed"}, status_code=202)


@app.post("/api/stop")
async def api_stop():
    if app.state.session:
        app.state.session.stop()
    return JSONResponse({"status": "stopping"}, status_code=202)


@app.post("/api/retry-failed")
async def api_retry():
    if app.state.session:
        app.state.session.retry_failed()
    return JSONResponse({"status": "retrying"}, status_code=202)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    session = app.state.session
    if session is None:
        await ws.send_json({"type": "log", "message": "Waiting for a scrape to start…", "level": "info"})
        return
    try:
        async for event in session.events():
            await ws.send_json(event)
    except WebSocketDisconnect:
        return


def shutdown_session():
    if app.state.session and getattr(app.state.session, "is_running", False):
        app.state.session.stop()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source venv/bin/activate && pytest tests/test_server.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/server.py tests/test_server.py
git commit -m "feat(backend): FastAPI server with command endpoints and WebSocket feed"
```

---

## Task 7: Frontend — `index.html` (two-pane layout markup)

**Files:**
- Create: `frontend/index.html`

- [ ] **Step 1: Write `frontend/index.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Udemy Transcript Scraper</title>
  <link rel="stylesheet" href="/static/style.css" />
</head>
<body>
  <div id="app"></div>
  <script src="/static/app.js"></script>
  <script>
    const ui = new ScraperUI();
    ui.mount(document.getElementById('app'));
    ui.bindControls();
    ui.connectWS();
  </script>
</body>
</html>
```

- [ ] **Step 2: Verify it loads (frontend not complete yet — just structure)**

Run: `source venv/bin/activate && python -c "import backend.server; print('ok')"`
Expected: `ok` (no import errors). Full visual verification happens after Task 9.

- [ ] **Step 3: Commit**

```bash
git add frontend/index.html
git commit -m "feat(frontend): two-pane layout HTML shell"
```

---

## Task 8: Frontend — `style.css` (Midnight Pro theme)

**Files:**
- Create: `frontend/style.css`

- [ ] **Step 1: Write `frontend/style.css`**

```css
:root {
  --bg-deep: #0b1120;
  --bg-card: #0f172a;
  --bg-elevated: #1e293b;
  --bg-sidebar: #0d1424;
  --border: #1e293b;
  --border-strong: #334155;
  --text-primary: #e2e8f0;
  --text-secondary: #94a3b8;
  --text-muted: #64748b;
  --accent: #6366f1;
  --accent-hover: #818cf8;
  --accent-glow: rgba(99,102,241,.35);
  --success: #10b981;
  --skip: #3b82f6;
  --fail: #ef4444;
  --pending: rgba(148,163,184,.13);
}

* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; }
body {
  font-family: -apple-system, "Segoe UI", Roboto, Inter, sans-serif;
  background: var(--bg-deep);
  color: var(--text-primary);
  overflow: hidden;
}
#app { display: flex; height: 100vh; }

/* ---------- Sidebar ---------- */
.sidebar {
  width: 300px; flex-shrink: 0;
  background: var(--bg-sidebar);
  border-right: 1px solid var(--border);
  display: flex; flex-direction: column;
  overflow-y: auto;
}
.brand { display: flex; align-items: center; gap: 10px; padding: 16px 18px; border-bottom: 1px solid var(--border); }
.logo { width: 28px; height: 28px; border-radius: 8px; background: linear-gradient(135deg,#6366f1,#8b5cf6);
  display: flex; align-items: center; justify-content: center; font-weight: 800; font-size: 14px; color: #fff; }
.brand-name { font-weight: 700; font-size: 14px; }
.brand-sub { font-size: 10px; color: var(--text-muted); }

.side-section { padding: 16px 18px; border-bottom: 1px solid #161f33; }
.side-label { font-size: 10px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase;
  color: var(--text-muted); margin-bottom: 10px; }
.field { margin-bottom: 12px; }
.field-label { font-size: 11px; color: var(--text-secondary); margin-bottom: 5px; display: block; }
.input {
  width: 100%; height: 34px; background: var(--bg-elevated);
  border: 1px solid var(--border-strong); border-radius: 7px;
  padding: 0 10px; color: var(--text-primary); font-size: 12px; font-family: inherit;
}
.input::placeholder { color: var(--text-muted); }
.input.invalid { border-color: var(--fail); }
.input-hint { color: var(--fail); font-size: 10px; margin-top: 4px; display: none; }
.input-hint.show { display: block; }
.input-row { display: flex; gap: 6px; }
.input-row .input { flex: 1; }
.btn-sm { height: 34px; padding: 0 12px; border-radius: 7px; background: #273449;
  color: #cbd5e1; font-size: 11px; font-weight: 600; border: 1px solid var(--border-strong); cursor: pointer; }

.slider-row { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.slider { flex: 1; -webkit-appearance: none; appearance: none; height: 4px; background: var(--border-strong);
  border-radius: 2px; outline: none; }
.slider::-webkit-slider-thumb { -webkit-appearance: none; width: 14px; height: 14px; border-radius: 50%;
  background: var(--accent); cursor: pointer; }
.slider-val { font-size: 12px; font-weight: 700; color: var(--accent-hover); min-width: 22px; text-align: right; }
.slider-cap { font-size: 10px; color: var(--text-muted); margin-left: 6px; }

.btn-primary { width: 100%; height: 38px; background: var(--accent); color: #fff; border: none;
  border-radius: 8px; font-size: 13px; font-weight: 700; cursor: pointer; box-shadow: 0 4px 14px var(--accent-glow); }
.btn-primary:hover { background: var(--accent-hover); }
.btn-primary:disabled { background: #2a2a3e; color: var(--text-muted); box-shadow: none; cursor: not-allowed; }
.btn-row { display: flex; gap: 8px; margin-top: 8px; }
.btn-ghost { flex: 1; height: 34px; background: transparent; border: 1px solid var(--border-strong);
  color: var(--text-secondary); border-radius: 7px; font-size: 11px; font-weight: 600; cursor: pointer; }
.btn-ghost:disabled { opacity: .4; cursor: not-allowed; }
.btn-stop { flex: 1; height: 34px; background: rgba(239,68,68,.12); border: 1px solid rgba(239,68,68,.4);
  color: #fca5a5; border-radius: 7px; font-size: 11px; font-weight: 600; cursor: pointer; }
.btn-stop:disabled { opacity: .4; cursor: not-allowed; }

/* ---------- Progress card ---------- */
.prog-card { background: #16203a; border: 1px solid var(--border); border-radius: 10px; padding: 14px; }
.prog-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 10px; }
.prog-title { font-size: 11px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: .06em; }
.prog-count { font-size: 15px; font-weight: 700; }
.prog-count span { color: var(--text-muted); font-weight: 500; }
.prog-bar { height: 8px; background: var(--bg-deep); border-radius: 4px; overflow: hidden; margin-bottom: 12px; }
.prog-fill { height: 100%; background: linear-gradient(90deg,#6366f1,#8b5cf6); border-radius: 4px; transition: width .3s; width: 0; }
.stats { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin-bottom: 10px; }
.stat { display: flex; align-items: center; gap: 6px; font-size: 11px; color: var(--text-secondary); }
.stat .dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.stat b { color: var(--text-primary); }
.stat.s .dot { background: var(--success); }
.stat.p .dot { background: var(--accent); }
.stat.k .dot { background: var(--skip); }
.stat.f .dot { background: var(--fail); }
.prog-status { font-size: 11px; color: var(--text-secondary); padding-top: 10px; border-top: 1px solid var(--border); }
.prog-status b { color: var(--text-primary); }
.prog-meta { display: flex; justify-content: space-between; font-size: 10px; color: var(--text-muted); margin-top: 6px; }

/* ---------- Main ---------- */
.main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.main-head { display: flex; align-items: center; justify-content: space-between;
  padding: 16px 22px; border-bottom: 1px solid var(--border); }
.course-title { font-size: 15px; font-weight: 700; }
.course-sub { font-size: 11px; color: var(--text-muted); margin-top: 2px; }
.pill { display: flex; align-items: center; gap: 6px; font-size: 10px; font-weight: 600;
  padding: 5px 10px; border-radius: 20px; }
.pill.idle { background: rgba(100,116,139,.15); color: var(--text-secondary); }
.pill.running { background: rgba(99,102,241,.12); border: 1px solid rgba(99,102,241,.3); color: #a5b4fc; }
.pill.done { background: rgba(16,185,129,.12); border: 1px solid rgba(16,185,129,.3); color: #6ee7b7; }
.pill.error { background: rgba(239,68,68,.12); border: 1px solid rgba(239,68,68,.3); color: #fca5a5; }
.live-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--accent-hover); animation: blip 1.2s infinite; }
@keyframes blip { 0%,100% { opacity: 1; } 50% { opacity: .3; } }

.sections { flex: 1; overflow-y: auto; padding: 16px 22px; }
.sec-card { background: var(--bg-card); border: 1px solid var(--border); border-radius: 10px;
  padding: 14px 16px; margin-bottom: 12px; }
.sec-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; cursor: pointer; }
.sec-title { font-size: 13px; font-weight: 700; }
.sec-title .idx { color: var(--accent); margin-right: 8px; }
.sec-meta { display: flex; align-items: center; gap: 10px; }
.sec-count { font-size: 11px; color: var(--text-secondary); }
.sec-count b { color: var(--text-primary); }
.sec-mini { width: 60px; height: 4px; background: var(--bg-elevated); border-radius: 2px; overflow: hidden; }
.sec-mini-fill { height: 100%; background: var(--success); transition: width .3s; }

.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(16px, 1fr)); gap: 5px; }
.grid.collapsed { display: none; }
.box { aspect-ratio: 1; border-radius: 4px; min-width: 14px; position: relative; cursor: pointer;
  transition: transform .12s, box-shadow .12s, background .25s; }
.box:hover { transform: scale(1.25); box-shadow: 0 0 0 2px var(--bg-deep), 0 0 0 3px var(--accent-hover); z-index: 5; }
.b-pending { background: var(--pending); }
.b-success { background: var(--success); }
.b-skip { background: var(--skip); opacity: .55; }
.b-fail { background: var(--fail); box-shadow: 0 0 8px rgba(239,68,68,.5); }
.b-working { background: var(--accent); }
.b-working::after { content: ""; position: absolute; inset: 0; border-radius: 4px; background: var(--accent-hover); animation: pulse 1.1s infinite; }
@keyframes pulse { 0%,100% { opacity: 1; transform: scale(1); } 50% { opacity: .35; transform: scale(.8); } }

/* tooltip */
.tip { position: fixed; background: var(--bg-elevated); border: 1px solid var(--border-strong);
  border-radius: 9px; padding: 10px 12px; width: 220px; box-shadow: 0 12px 30px rgba(0,0,0,.5); z-index: 100;
  pointer-events: none; display: none; }
.tip.show { display: block; }
.tip-title { font-size: 12px; font-weight: 600; margin-bottom: 8px; line-height: 1.3; }
.tip-row { display: flex; justify-content: space-between; font-size: 11px; margin-bottom: 4px; }
.tip-row .k { color: var(--text-muted); }
.tip-row .v { color: #cbd5e1; }

/* empty state */
.empty { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 12px; color: var(--text-muted); }
.empty-mark { font-size: 40px; opacity: .4; }
.empty p { font-size: 14px; }

/* error banner */
.banner { margin: 12px 22px 0; background: rgba(239,68,68,.1); border: 1px solid rgba(239,68,68,.4);
  color: #fca5a5; padding: 10px 14px; border-radius: 8px; font-size: 12px; display: none; }
.banner.show { display: block; }

/* log bar */
.log-bar { display: flex; align-items: center; justify-content: space-between; padding: 10px 22px;
  border-top: 1px solid var(--border); background: var(--bg-sidebar); cursor: pointer; }
.log-bar-l { display: flex; align-items: center; gap: 10px; }
.log-chev { color: var(--text-muted); font-size: 10px; }
.log-title { font-size: 11px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: .06em; }
.log-preview { font-family: "SF Mono", Consolas, monospace; font-size: 10.5px; color: var(--text-muted); }
.log-panel { height: 0; overflow: hidden; background: var(--bg-deep); transition: height .2s; }
.log-panel.open { height: 140px; }
.log-box { height: 140px; overflow-y: auto; padding: 8px 22px; font-family: "SF Mono", Consolas, monospace;
  font-size: 10.5px; color: var(--text-secondary); }
.log-line.ok { color: var(--success); }
.log-line.fail { color: var(--fail); }
.log-line.warn { color: #fbbf24; }

/* toast */
.toast { position: fixed; right: 22px; bottom: 22px; background: var(--bg-elevated);
  border: 1px solid var(--border-strong); border-radius: 10px; padding: 14px 16px; min-width: 260px;
  box-shadow: 0 12px 30px rgba(0,0,0,.5); transform: translateY(120%); transition: transform .3s; z-index: 200; }
.toast.show { transform: translateY(0); }
.toast-title { font-size: 13px; font-weight: 700; margin-bottom: 4px; }
.toast-body { font-size: 11px; color: var(--text-secondary); margin-bottom: 10px; }
.toast button { background: var(--accent); color: #fff; border: none; border-radius: 6px;
  padding: 6px 12px; font-size: 11px; font-weight: 600; cursor: pointer; }
```

- [ ] **Step 2: Commit**

```bash
git add frontend/style.css
git commit -m "feat(frontend): Midnight Pro theme stylesheet"
```

---

## Task 9: Frontend — `app.js` (ScraperUI module)

**Files:**
- Create: `frontend/app.js`

- [ ] **Step 1: Write `frontend/app.js`**

```javascript
function ScraperUI() {
  this.state = {
    phase: 'idle',
    course: { title: '', sections: [] },
    overall: { completed: 0, total: 0, failed: 0, skipped: 0, active: 0, success: 0, elapsedMs: 0 },
  };
  this.els = {};
  this.ws = null;
  this.startedAt = null;
}

ScraperUI.prototype.mount = function (container) {
  container.innerHTML = `
    <aside class="sidebar">
      <div class="brand">
        <div class="logo">U</div>
        <div><div class="brand-name">Udemy Scraper</div><div class="brand-sub">Transcript downloader</div></div>
      </div>
      <div class="side-section">
        <div class="side-label">Configuration</div>
        <div class="field">
          <label class="field-label">Course URL</label>
          <input class="input" id="url" placeholder="https://www.udemy.com/course/your-course/learn" />
          <div class="input-hint" id="url-hint">Enter a valid Udemy course URL</div>
        </div>
        <div class="field">
          <label class="field-label">Save to</label>
          <div class="input-row">
            <input class="input" id="dir" value="${this._defaultDir()}" />
            <button class="btn-sm" id="browse">Browse</button>
          </div>
        </div>
        <div class="slider-row">
          <label class="field-label" style="margin:0">Batch size</label>
          <input type="range" class="slider" id="batch" min="1" max="15" value="5" />
          <div class="slider-val" id="batch-val">5</div>
        </div>
        <div style="font-size:10px;color:var(--text-muted);margin-bottom:10px">lectures per batch</div>
        <div class="slider-row">
          <label class="field-label" style="margin:0">Threads</label>
          <input type="range" class="slider" id="threads" min="1" max="6" value="3" style="max-width:80px" />
          <div class="slider-val" id="threads-val">3</div>
          <div class="slider-cap">parallel workers</div>
        </div>
        <button class="btn-primary" id="start">Start Scraping</button>
        <div class="btn-row">
          <button class="btn-ghost" id="resume" disabled>Resume</button>
          <button class="btn-stop" id="stop" disabled>Stop</button>
        </div>
      </div>
      <div class="side-section">
        <div class="side-label">Overall Progress</div>
        <div class="prog-card">
          <div class="prog-head">
            <span class="prog-title">Course</span>
            <span class="prog-count" id="prog-count">0 <span>/ 0</span></span>
          </div>
          <div class="prog-bar"><div class="prog-fill" id="prog-fill"></div></div>
          <div class="stats">
            <div class="stat s"><span class="dot"></span><span><b id="st-success">0</b> success</span></div>
            <div class="stat p"><span class="dot"></span><span><b id="st-active">0</b> active</span></div>
            <div class="stat k"><span class="dot"></span><span><b id="st-skipped">0</b> skipped</span></div>
            <div class="stat f"><span class="dot"></span><span><b id="st-failed">0</b> failed</span></div>
          </div>
          <div class="prog-status" id="prog-status">Ready</div>
          <div class="prog-meta"><span id="elapsed">Elapsed 0:00</span><span id="eta">ETA —</span></div>
        </div>
      </div>
    </aside>
    <main class="main">
      <div class="main-head">
        <div><div class="course-title" id="course-title">No course loaded</div>
        <div class="course-sub" id="course-sub"></div></div>
        <div class="pill idle" id="phase-pill">IDLE</div>
      </div>
      <div class="banner" id="banner"></div>
      <div class="sections" id="sections">
        <div class="empty"><div class="empty-mark">▸</div><p>Paste a course URL to begin</p></div>
      </div>
      <div class="log-panel" id="log-panel"><div class="log-box" id="log-box"></div></div>
      <div class="log-bar" id="log-bar">
        <div class="log-bar-l"><span class="log-chev" id="log-chev">▸</span><span class="log-title">Activity Log</span></div>
        <div class="log-preview" id="log-preview">—</div>
      </div>
    </main>
    <div class="tip" id="tip"></div>
    <div class="toast" id="toast">
      <div class="toast-title" id="toast-title"></div>
      <div class="toast-body" id="toast-body"></div>
      <button id="toast-btn">Retry failed</button>
    </div>
  `;
  this.els = {
    url: document.getElementById('url'), dir: document.getElementById('dir'),
    batch: document.getElementById('batch'), batchVal: document.getElementById('batch-val'),
    threads: document.getElementById('threads'), threadsVal: document.getElementById('threads-val'),
    start: document.getElementById('start'), resume: document.getElementById('resume'),
    stop: document.getElementById('stop'), browse: document.getElementById('browse'),
    progCount: document.getElementById('prog-count'), progFill: document.getElementById('prog-fill'),
    progStatus: document.getElementById('prog-status'), elapsed: document.getElementById('elapsed'),
    eta: document.getElementById('eta'), stSuccess: document.getElementById('st-success'),
    stActive: document.getElementById('st-active'), stSkipped: document.getElementById('st-skipped'),
    stFailed: document.getElementById('st-failed'), courseTitle: document.getElementById('course-title'),
    courseSub: document.getElementById('course-sub'), phasePill: document.getElementById('phase-pill'),
    banner: document.getElementById('banner'), sections: document.getElementById('sections'),
    logPanel: document.getElementById('log-panel'), logBox: document.getElementById('log-box'),
    logBar: document.getElementById('log-bar'), logChev: document.getElementById('log-chev'),
    logPreview: document.getElementById('log-preview'), tip: document.getElementById('tip'),
    toast: document.getElementById('toast'), toastTitle: document.getElementById('toast-title'),
    toastBody: document.getElementById('toast-body'), toastBtn: document.getElementById('toast-btn'),
    urlHint: document.getElementById('url-hint'),
  };
};

ScraperUI.prototype._defaultDir = function () {
  return '~/Desktop/Udemy_Transcripts';
};

ScraperUI.prototype.bindControls = function () {
  const self = this;
  this.els.batch.addEventListener('input', () => { this.els.batchVal.textContent = this.els.batch.value; });
  this.els.threads.addEventListener('input', () => { this.els.threadsVal.textContent = this.els.threads.value; });

  this.els.start.addEventListener('click', () => this._start());
  this.els.resume.addEventListener('click', () => this._resume());
  this.els.stop.addEventListener('click', () => this._stop());
  this.els.toastBtn.addEventListener('click', () => { this._retryFailed(); this._hideToast(); });
  this.els.browse.addEventListener('click', () => this._browse());

  this.els.url.addEventListener('keydown', (e) => { if (e.key === 'Enter') this._start(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && this.state.phase === 'running') this._stop(); });

  this.els.url.addEventListener('input', () => {
    this.els.url.classList.remove('invalid');
    this.els.urlHint.classList.remove('show');
  });

  this.els.logBar.addEventListener('click', () => {
    const open = this.els.logPanel.classList.toggle('open');
    this.els.logChev.textContent = open ? '▾' : '▸';
  });

  this.els.sections.addEventListener('mouseover', (e) => this._onBoxHover(e));
  this.els.sections.addEventListener('mouseout', () => this.els.tip.classList.remove('show'));
  this.els.sections.addEventListener('click', (e) => {
    const head = e.target.closest('.sec-head');
    if (head) head.parentElement.querySelector('.grid').classList.toggle('collapsed');
  });

  window.addEventListener('beforeunload', (e) => {
    if (self.state.phase === 'running') { e.preventDefault(); e.returnValue = ''; }
  });
};

ScraperUI.prototype._browse = async function () {
  if (window.pywebview && window.pywebview.api) {
    const p = await window.pywebview.api.browse_directory();
    if (p) this.els.dir.value = p;
  } else {
    const p = prompt('Output directory:', this.els.dir.value);
    if (p) this.els.dir.value = p;
  }
};

ScraperUI.prototype._start = function () {
  const url = this.els.url.value.trim();
  if (!/https?:\/\/www\.udemy\.com\/course\//.test(url)) {
    this.els.url.classList.add('invalid');
    this.els.urlHint.classList.add('show');
    return;
  }
  this._post('/api/start', {
    url, outputDir: this.els.dir.value.trim(),
    batchSize: +this.els.batch.value, numThreads: +this.els.threads.value,
  });
};

ScraperUI.prototype._resume = function () {
  this._post('/api/resume', { outputDir: this.els.dir.value.trim() });
};

ScraperUI.prototype._stop = function () {
  this._post('/api/stop', {});
};

ScraperUI.prototype._retryFailed = function () {
  this._post('/api/retry-failed', {});
};

ScraperUI.prototype._post = async function (path, body) {
  try {
    await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  } catch (e) { /* server may be restarting */ }
};

ScraperUI.prototype.connectWS = function () {
  const url = (location.protocol === 'http:' ? 'ws://' + location.host : 'ws://localhost:8765') + '/ws';
  this.ws = new WebSocket(url);
  this.ws.onmessage = (m) => this.applyEvent(JSON.parse(m.data));
  this.ws.onclose = () => {
    if (this.state.phase === 'running') {
      this._setPhase('running', 'RECONNECTING…');
      setTimeout(() => this.connectWS(), 1000);
    }
  };
};

ScraperUI.prototype.applyEvent = function (ev) {
  switch (ev.type) {
    case 'course_discovered': this._onDiscovered(ev); break;
    case 'lecture_status': this._onLecture(ev); break;
    case 'progress': this._onProgress(ev); break;
    case 'log': this._onLog(ev); break;
    case 'done': this._onDone(ev); break;
    case 'error': this._onError(ev); break;
  }
};

ScraperUI.prototype._onDiscovered = function (ev) {
  this.state.course = { title: ev.courseTitle, sections: ev.sections.map(s => ({
    index: s.index, title: s.title,
    lectures: s.lectures.map(l => ({ index: l.index, id: l.id, title: l.title, status: 'pending', message: '', size: null })),
  })) };
  this.els.courseTitle.textContent = ev.courseTitle;
  const total = ev.sections.reduce((n, s) => n + s.lectures.length, 0);
  this.els.courseSub.textContent = `${ev.sections.length} sections · ${total} lectures`;
  this._renderSections();
  this._setPhase('running', 'RUNNING');
  this.startedAt = Date.now();
};

ScraperUI.prototype._onLecture = function (ev) {
  const sec = this.state.course.sections[ev.sectionIdx];
  if (!sec) return;
  const lec = sec.lectures[ev.lectureIdx];
  if (!lec) return;
  lec.status = ev.status;
  lec.message = ev.message || '';
  if (ev.size !== undefined && ev.size !== null) lec.size = ev.size;
  const box = this.els.sections.querySelector(`[data-sec="${ev.sectionIdx}"][data-lec="${ev.lectureIdx}"]`);
  if (box) {
    box.className = 'box b-' + ({ 'in-progress': 'working', success: 'success', skipped: 'skip', failed: 'fail', pending: 'pending' }[ev.status] || 'pending');
  }
  this._updateSectionMeta(ev.sectionIdx);
  this.els.progStatus.innerHTML = `Scraping: <b>${lec.title}</b>`;
};

ScraperUI.prototype._onProgress = function (ev) {
  this.state.overall = ev;
  this._updateOverall();
};

ScraperUI.prototype._onLog = function (ev) {
  const line = document.createElement('div');
  line.className = 'log-line ' + ({ success: 'ok', error: 'fail', warn: 'warn', info: '' }[ev.level] || '');
  const ts = new Date().toLocaleTimeString('en-GB');
  line.textContent = `[${ts}] ${ev.message}`;
  this.els.logBox.appendChild(line);
  this.els.logBox.scrollTop = this.els.logBox.scrollHeight;
  this.els.logPreview.textContent = line.textContent;
  if (this.els.logBox.children.length > 500) this.els.logBox.removeChild(this.els.logBox.firstChild);
};

ScraperUI.prototype._onDone = function (ev) {
  this.state.overall = { ...this.state.overall, ...ev };
  this._updateOverall();
  this._setPhase('done', 'DONE');
  this._showToast('Done', `${ev.completed} saved · ${ev.failed} failed · ${ev.skipped} skipped`, ev.failed > 0);
  this.els.progStatus.textContent = `Done — ${ev.completed} completed, ${ev.failed} failed`;
  this.els.progFill.style.width = '100%';
  this._resetButtons(true);
};

ScraperUI.prototype._onError = function (ev) {
  this._setPhase('error', 'ERROR');
  this.els.banner.textContent = ev.message + ' — open Chrome, log into Udemy, then retry.';
  this.els.banner.classList.add('show');
  this._resetButtons(true);
};

ScraperUI.prototype._renderSections = function () {
  this.els.sections.innerHTML = '';
  this.state.course.sections.forEach((sec, si) => {
    const card = document.createElement('div');
    card.className = 'sec-card';
    card.innerHTML = `
      <div class="sec-head">
        <div class="sec-title"><span class="idx">${String(sec.index).padStart(2, '0')}</span>${this._esc(sec.title)}</div>
        <div class="sec-meta">
          <div class="sec-count"><b id="cnt-${si}">0</b>/${sec.lectures.length}</div>
          <div class="sec-mini"><div class="sec-mini-fill" id="mini-${si}" style="width:0"></div></div>
        </div>
      </div>
      <div class="grid" id="grid-${si}"></div>`;
    const grid = card.querySelector(`#grid-${si}`);
    sec.lectures.forEach((lec, li) => {
      const box = document.createElement('div');
      box.className = 'box b-pending';
      box.dataset.sec = si; box.dataset.lec = li;
      box.dataset.title = lec.title;
      grid.appendChild(box);
    });
    this.els.sections.appendChild(card);
  });
};

ScraperUI.prototype._updateSectionMeta = function (si) {
  const sec = this.state.course.sections[si];
  const done = sec.lectures.filter(l => l.status === 'success' || l.status === 'skipped').length;
  const cnt = document.getElementById(`cnt-${si}`);
  const mini = document.getElementById(`mini-${si}`);
  if (cnt) cnt.textContent = done;
  if (mini) mini.style.width = (sec.lectures.length ? (done / sec.lectures.length) * 100 : 0) + '%';
};

ScraperUI.prototype._updateOverall = function () {
  const o = this.state.overall;
  this.els.progCount.innerHTML = `${o.completed} <span>/ ${o.total}</span>`;
  const pct = o.total ? (o.completed / o.total) * 100 : 0;
  this.els.progFill.style.width = pct + '%';
  this.els.stSuccess.textContent = o.success ?? 0;
  this.els.stActive.textContent = o.active ?? 0;
  this.els.stSkipped.textContent = o.skipped ?? 0;
  this.els.stFailed.textContent = o.failed ?? 0;
  const secs = Math.floor((o.elapsedMs || 0) / 1000);
  this.els.elapsed.textContent = `Elapsed ${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`;
  if (o.completed > 0 && o.total > 0 && o.completed < o.total) {
    const rate = (o.elapsedMs || 1) / o.completed;
    const remain = Math.max(0, (o.total - o.completed) * rate / 1000);
    const rm = Math.floor(remain / 60);
    this.els.eta.textContent = `ETA ~${rm}m`;
  } else if (o.completed >= o.total && o.total > 0) {
    this.els.eta.textContent = 'ETA —';
  }
};

ScraperUI.prototype._setPhase = function (phase, label) {
  this.state.phase = phase;
  const pill = this.els.phasePill;
  pill.className = 'pill ' + phase;
  pill.innerHTML = (phase === 'running') ? `<span class="live-dot"></span> ${label || 'RUNNING'}` : (label || phase.toUpperCase());
  if (phase === 'running') { this._lockControls(true); }
  else if (phase === 'error' || phase === 'done') { this.els.banner.classList.toggle('show', phase === 'error'); }
};

ScraperUI.prototype._lockControls = function (locked) {
  this.els.start.disabled = locked;
  this.els.resume.disabled = locked;
  this.els.stop.disabled = !locked;
  this.els.url.disabled = locked; this.els.dir.disabled = locked;
  this.els.batch.disabled = locked; this.els.threads.disabled = locked;
};

ScraperUI.prototype._resetButtons = function (idle) {
  this.els.start.disabled = false;
  this.els.stop.disabled = true;
  this.els.url.disabled = false; this.els.dir.disabled = false;
  this.els.batch.disabled = false; this.els.threads.disabled = false;
};

ScraperUI.prototype._onBoxHover = function (e) {
  const box = e.target.closest('.box');
  if (!box) { this.els.tip.classList.remove('show'); return; }
  const si = +box.dataset.sec, li = +box.dataset.lec;
  const sec = this.state.course.sections[si];
  const lec = sec ? sec.lectures[li] : null;
  if (!lec) return;
  const statusLabel = { pending: 'Pending', 'in-progress': 'In progress', success: 'Saved', skipped: 'Skipped', failed: 'Failed' }[lec.status] || lec.status;
  this.els.tip.innerHTML = `
    <div class="tip-title">${this._esc(lec.title)}</div>
    <div class="tip-row"><span class="k">Status</span><span class="v">${statusLabel}</span></div>
    <div class="tip-row"><span class="k">Size</span><span class="v">${lec.size ? lec.size.toLocaleString() + ' chars' : '—'}</span></div>
    <div class="tip-row"><span class="k">Lecture</span><span class="v">#${li + 1} of ${sec.lectures.length}</span></div>`;
  this.els.tip.classList.add('show');
  const r = box.getBoundingClientRect();
  this.els.tip.style.left = Math.min(r.left, window.innerWidth - 240) + 'px';
  this.els.tip.style.top = (r.top - this.els.tip.offsetHeight - 10) + 'px';
};

ScraperUI.prototype._showToast = function (title, body, showRetry) {
  this.els.toastTitle.textContent = title;
  this.els.toastBody.textContent = body;
  this.els.toastBtn.style.display = showRetry ? '' : 'none';
  this.els.toast.classList.add('show');
};

ScraperUI.prototype._hideToast = function () { this.els.toast.classList.remove('show'); };

ScraperUI.prototype._esc = function (s) {
  const d = document.createElement('div'); d.textContent = s; return d.innerHTML;
};

window.ScraperUI = ScraperUI;
```

- [ ] **Step 2: Smoke-verify (no JS errors on load)**

Run: `source venv/bin/activate && python -c "import backend.server; print('server ok')"` then open `frontend/index.html` served by the server in a browser (Task 11 wires the launcher). For now, just confirm the file parses:
Run: `node --check frontend/app.js 2>/dev/null && echo "js ok" || echo "node not available — skip"`
Expected: `js ok` (or "node not available — skip" if node isn't installed; full verification in Task 10).

- [ ] **Step 3: Commit**

```bash
git add frontend/app.js
git commit -m "feat(frontend): ScraperUI module — state, render, WS, commands"
```

---

## Task 10: Frontend — `dev-harness.html` (canned-event replay)

**Files:**
- Create: `frontend/dev-harness.html`

- [ ] **Step 1: Write `frontend/dev-harness.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>Dev Harness — ScraperUI replay</title>
  <link rel="stylesheet" href="/static/style.css" />
</head>
<body>
  <div id="app"></div>
  <script src="/static/app.js"></script>
  <script>
    const CANNED = [
      { type: 'course_discovered', courseTitle: 'React - The Complete Guide 2024',
        sections: [
          { index: 1, title: 'Introduction', lectures: [
            { index: 0, id: 'l1', title: 'Welcome' }, { index: 1, id: 'l2', title: 'How to Use' },
            { index: 2, id: 'l3', title: 'Let\'s Go' } ] },
          { index: 2, title: 'React Basics', lectures: [
            { index: 0, id: 'l4', title: 'Components' }, { index: 1, id: 'l5', title: 'Props' },
            { index: 2, id: 'l6', title: 'State' }, { index: 3, id: 'l7', title: 'Events' },
            { index: 4, id: 'l8', title: 'Quiz 1' }, { index: 5, id: 'l9', title: 'Lists' } ] },
        ] },
      { type: 'lecture_status', sectionIdx: 0, lectureIdx: 0, status: 'in-progress', message: 'working', size: null },
      { type: 'lecture_status', sectionIdx: 0, lectureIdx: 0, status: 'success', message: 'Saved', size: 1200 },
      { type: 'lecture_status', sectionIdx: 0, lectureIdx: 1, status: 'success', message: 'Saved', size: 980 },
      { type: 'lecture_status', sectionIdx: 0, lectureIdx: 2, status: 'skipped', message: 'no captions', size: null },
      { type: 'progress', completed: 3, total: 9, failed: 0, skipped: 1, active: 0, success: 2, elapsedMs: 4000 },
      { type: 'lecture_status', sectionIdx: 1, lectureIdx: 0, status: 'in-progress', message: 'working', size: null },
      { type: 'lecture_status', sectionIdx: 1, lectureIdx: 1, status: 'in-progress', message: 'working', size: null },
      { type: 'lecture_status', sectionIdx: 1, lectureIdx: 0, status: 'success', message: 'Saved', size: 2200 },
      { type: 'lecture_status', sectionIdx: 1, lectureIdx: 2, status: 'failed', message: 'vtt_error', size: null },
      { type: 'lecture_status', sectionIdx: 1, lectureIdx: 4, status: 'skipped', message: 'api_error', size: null },
      { type: 'progress', completed: 6, total: 9, failed: 1, skipped: 2, active: 1, success: 3, elapsedMs: 12000 },
      { type: 'lecture_status', sectionIdx: 1, lectureIdx: 1, status: 'success', message: 'Saved', size: 3100 },
      { type: 'done', completed: 8, total: 9, failed: 1, skipped: 2, active: 0, success: 5, elapsedMs: 21000 },
    ];

    const ui = new ScraperUI();
    ui.mount(document.getElementById('app'));
    ui.bindControls();
    let i = 0;
    const tick = () => {
      if (i >= CANNED.length) return;
      ui.applyEvent(CANNED[i++]);
      setTimeout(tick, 700);
    };
    setTimeout(tick, 400);
  </script>
</body>
</html>
```

- [ ] **Step 2: Visual verification**

Run: `source venv/bin/activate && uvicorn backend.server:app --port 8765 &` then open `http://localhost:8765/static/dev-harness.html` in a browser, then `kill %1` when done.
Expected: the canned sequence plays — boxes transition pending→in-progress (pulsing)→success/skipped/failed, the overall card updates counts + ETA, a section mini-bar fills, hovering a box shows the tooltip, and a completion toast appears with a "Retry failed" button (since 1 failed). Clicking "Retry failed" POSTs to `/api/retry-failed` (no-op here).

- [ ] **Step 3: Commit**

```bash
git add frontend/dev-harness.html
git commit -m "feat(frontend): dev-harness for canned-event visual verification"
```

---

## Task 11: PyWebView launcher (`app.py` rewrite) + serve `index.html` at `/`

**Files:**
- Rewrite: `app.py`
- Modify: `backend/server.py` (serve `index.html` at `/` instead of the JSON health check)

- [ ] **Step 1: Update `backend/server.py` root route to serve `index.html`**

Replace the `@app.get("/")` handler in `backend/server.py`:

```python
from fastapi.responses import FileResponse

@app.get("/")
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")
```

(Add the `FileResponse` import at the top alongside the existing `JSONResponse` import.)

- [ ] **Step 2: Rewrite `app.py`**

```python
import threading
import time
import urllib.request

import uvicorn
import webview

from backend.server import app, shutdown_session

HOST = "127.0.0.1"
PORT = 8765


class JsApi:
    def browse_directory(self):
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        path = filedialog.askdirectory(title="Select Output Directory")
        root.destroy()
        return path or ""


def _wait_for_server(url, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=0.5)
            return True
        except Exception:
            time.sleep(0.15)
    return False


def _run_server():
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


def main():
    server_thread = threading.Thread(target=_run_server, daemon=True)
    server_thread.start()

    url = f"http://{HOST}:{PORT}/"
    if not _wait_for_server(url):
        print("Failed to start local server. Aborting.")
        return

    webview.create_window(
        "Udemy Transcript Scraper", url,
        width=1040, height=720, min_size=(800, 600),
        js_api=JsApi(),
    )
    webview.start()
    shutdown_session()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Update `run.sh` (no change needed — confirm)**

`run.sh` already runs `source venv/bin/activate && python app.py`. Verify it still matches:
Run: `cat run.sh`
Expected: the existing content (no change required).

- [ ] **Step 4: Smoke-verify the launcher starts**

Run: `source venv/bin/activate && timeout 8 python app.py & sleep 5 && curl -s http://127.0.0.1:8765/ | head -c 80; kill %1 2>/dev/null`
Expected: the first bytes of `index.html` (e.g. `<!DOCTYPE html>`) print, then the process is killed. (PyWebView's GUI window won't open in a headless shell, but the server serving `/` proves the wiring.)

- [ ] **Step 5: Commit**

```bash
git add app.py backend/server.py
git commit -m "feat: PyWebView launcher serving the web UI at /"
```

---

## Task 12: End-to-end verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `source venv/bin/activate && pytest -v`
Expected: all tests PASS (test_extract_slug, test_progress_tracker, test_scraper_events, test_scraper_session, test_server).

- [ ] **Step 2: Dev-harness replay shows all states + interactions**

Run: `source venv/bin/activate && uvicorn backend.server:app --port 8765 &` → open `http://localhost:8765/static/dev-harness.html` → `kill %1`
Expected (visual): pending→in-progress pulse→success/skipped/failed transitions cross-fade smoothly; overall card counts + ETA update; section mini-bars fill; hover tooltip shows title/size/lecture#; completion toast appears with Retry button. No console errors (check devtools).

- [ ] **Step 3: App launches into empty state**

Run: `source venv/bin/activate && python app.py`
Expected: a 1040×720 desktop window titled "Udemy Transcript Scraper" opens showing the Midnight Pro layout with the empty-state placeholder ("Paste a course URL to begin") in the main area and the sidebar config + progress card.

- [ ] **Step 4: Real scrape against a small Udemy course (manual)**

With Chrome open and logged into Udemy, paste a small course URL in the app, set batch=5 / threads=3, click Start.
Expected: discovery fires, section cards + box grids appear, boxes light up per-lecture with **no flicker or race**, the overall bar + counts advance smoothly, Stop halts after the current batch, and on completion the toast appears. Verify resume: relaunch, the resume state hydrates already-done lectures green, then continues. Verify retry: if any failed, click "Retry failed" in the toast — red boxes go in-progress then green.

- [ ] **Step 5: Close-during-run guard + no orphaned processes**

While a scrape is running, close the window.
Expected: a browser confirm dialog appears ("Changes you made may not be saved" / beforeunload). Confirming closes the window; verify with `pgrep -f browser-use` that no `browser-use` subprocesses remain (the stop flag + daemon thread shutdown should clean up).

- [ ] **Step 6: Final commit (if any fixups were made during verification)**

```bash
git add -A
git commit -m "chore: verification fixups for UI overhaul" || echo "nothing to commit"
```

---

## Self-Review Notes (already applied)

- **Spec coverage:** every spec section maps to a task — architecture (T1, T5, T6, T11), layout (T7, T8), box system (T9, T10), event protocol (T5, T6, T9), polish (T9 — empty state, toast, inline validation, keyboard, beforeunload, section collapse, hover), error handling (T6, T9 — banner, reconnect, validation), testing (T2, T3, T4, T5, T6, T10, T12), migration (T1, T4, T11).
- **Placeholder scan:** no TBD/TODO; every code step contains complete code.
- **Type/name consistency:** `normalize_status` (T5) used consistently; `lecture_status.status` values are the box-state vocab (`in-progress`/`success`/`skipped`/`failed`) emitted by ScraperSession, consumed by `_onLecture` in T9 with matching class-map; `ScraperSession.events()`/`start()`/`stop()`/`retry_failed()` names match between T5, T6, T9; `set_session_factory` matches between T6 test and impl.
