# Selenium Engine Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the browser-use subprocess bridge in `scraper.py` with a persistent `undetected-chromedriver` instance, simplify the session threading model to single-driver sequential, and harden error handling — without changing the frontend ↔ backend contract.

**Architecture:** A new `driver.py` module holds a single shared `SeleniumDriverManager` (persistent Chrome profile via `undetected-chromedriver`). All in-page JS runs through `driver.execute_async_script` with a callback shim. `scraper.py` keeps its public method names but routes JS calls through the manager. `backend/scraper_session.py` drops its multi-threaded worker model for a single sequential batch loop. Error handling splits into auth failures, script timeouts (split-batch fallback), and stale-driver reconnection.

**Tech Stack:** Python 3.13, `undetected-chromedriver` 3.5.5, `selenium` 4.9+, FastAPI, pytest, Chrome 148.

**Spec:** `docs/superpowers/specs/2026-07-10-selenium-engine-design.md`

---

## File Structure

- **Create:** `driver.py` — `SeleniumDriverManager` class managing the persistent Chrome lifecycle, callback-shim JS execution, login detection, and reconnection. Held as a single shared instance at module scope.
- **Modify:** `scraper.py` — Replace `run_browser_use`/`_js`/`_js_json`/`connect_and_navigate` internals with `SeleniumDriverManager` calls. Rework `fetch_transcripts_batch` split-batch fallback. Rewrite `scrape_parallel` to sequential. Keep public method names.
- **Modify:** `backend/scraper_session.py` — Replace worker-thread model in `_run()` with a sequential batch loop. Update `retry_failed()` to use the shared driver. Add driver cleanup on stop/shutdown.
- **Modify:** `requirements.txt` — Add `undetected-chromedriver>=3.5.5` and `selenium>=4.9`. Remove browser-use comment.
- **Create:** `tests/test_driver_manager.py` — Unit tests for the callback shim, profile-dir resolution, version detection, login-wall check. Mocks `uc.Chrome`.
- **Modify:** `tests/test_scraper_session.py` — Update to mock `SeleniumDriverManager` instead of `run_browser_use`. Tests for sequential loop, stop_flag, retry, login wall.

**Unchanged:** `app.py`, `backend/server.py`, `frontend/*`, `progress_tracker.py`, `tests/test_extract_slug.py`, `tests/test_progress_tracker.py`, `tests/test_server.py`.

---

### Task 1: Create the feature branch

**Files:**
- (no file changes — branch setup only)

- [ ] **Step 1: Ensure main is current and create the feature branch**

Run:
```bash
git checkout main && git pull && git checkout -b feature/selenium-engine
```

Expected: `Switched to a new branch 'feature/selenium-engine'`. If `git pull` fails (no upstream), that's fine — proceed.

- [ ] **Step 2: Confirm branch**

Run: `git branch --show-current`
Expected: `feature/selenium-engine`

- [ ] **Step 3: Confirm working tree is clean**

Run: `git status`
Expected: `nothing to commit, working tree clean` (or only the previously-deleted `AGENTS.md` untracked change, which is unrelated). If there are staged changes, stop and ask the user before proceeding.

---

### Task 2: Add dependencies to requirements.txt

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Write the failing test**

There is no code test for a requirements file. The verification here is that `pip install` succeeds and the imports resolve. We'll verify in Task 3's tests. For now, just update the file.

- [ ] **Step 2: Update requirements.txt**

Read the current `requirements.txt` (6 lines). Replace its contents with:

```
fastapi>=0.110
uvicorn[standard]>=0.27
pywebview>=5.0
pytest>=8.0
httpx>=0.27
undetected-chromedriver>=3.5.5
selenium>=4.9
```

The browser-use comment line (`# browser-use is invoked via subprocess...`) is removed — browser-use is no longer a dependency.

- [ ] **Step 3: Install the new dependencies**

Run:
```bash
pip install -r requirements.txt
```

Expected: `undetected-chromedriver` and `selenium` install successfully. If `undetected-chromedriver` pulls a conflicting selenium version, pin `selenium>=4.9,<5` to stay within uc 3.5.5's supported range.

- [ ] **Step 4: Verify imports resolve**

Run:
```bash
python -c "import undetected_chromedriver as uc; import selenium; from selenium.common.exceptions import TimeoutException, JavascriptException, WebDriverException, InvalidSessionIdException; print('ok', uc.__version__, selenium.__version__)"
```

Expected: `ok 3.5.5 4.x.y` (or similar). If any import fails, resolve before continuing.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt
git commit -m "Add undetected-chromedriver and selenium dependencies"
```

---

### Task 3: Create driver.py — SeleniumDriverManager with callback shim

**Files:**
- Create: `driver.py`
- Create: `tests/test_driver_manager.py`

This task builds `SeleniumDriverManager` bottom-up: profile-dir resolution → callback-shim wrapping → `execute_async_js` → `connect` → `is_logged_in`/`ensure_logged_in` → `reconnect`/`quit`. Each piece is testable without a real Chrome (mock `uc.Chrome`).

- [ ] **Step 1: Write the failing test for profile-dir resolution**

Write `tests/test_driver_manager.py`:

```python
import os
from unittest.mock import patch, MagicMock
from driver import SeleniumDriverManager


def test_profile_dir_defaults_to_home():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("UDEMY_SCRAPER_PROFILE", None)
        mgr = SeleniumDriverManager()
        assert mgr.profile_dir == os.path.expanduser("~/.udemy-scraper-profile")


def test_profile_dir_respects_env_var(tmp_path):
    custom = str(tmp_path / "custom-profile")
    with patch.dict(os.environ, {"UDEMY_SCRAPER_PROFILE": custom}):
        mgr = SeleniumDriverManager()
        assert mgr.profile_dir == custom
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_driver_manager.py::test_profile_dir_defaults_to_home -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'driver'`

- [ ] **Step 3: Write minimal driver.py — profile-dir resolution only**

Write `driver.py`:

```python
"""
SeleniumDriverManager: persistent undetected-chromedriver lifecycle.
Held as a single shared instance so the driver survives across
ScraperSession instances (needed for the first-run login flow).
"""
import os


class SeleniumDriverManager:
    PROFILE_DIR_DEFAULT = "~/.udemy-scraper-profile"

    def __init__(self, profile_dir=None, version_main=148):
        if profile_dir is None:
            env = os.environ.get("UDEMY_SCRAPER_PROFILE")
            if env:
                profile_dir = env
            else:
                profile_dir = os.path.expanduser(self.PROFILE_DIR_DEFAULT)
        self.profile_dir = profile_dir
        self.version_main = version_main
        self._driver = None
        self._reconnecting = False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_driver_manager.py::test_profile_dir_defaults_to_home tests/test_driver_manager.py::test_profile_dir_respects_env_var -v`
Expected: PASS (both)

- [ ] **Step 5: Commit**

```bash
git add driver.py tests/test_driver_manager.py
git commit -m "feat: SeleniumDriverManager profile-dir resolution"
```

- [ ] **Step 6: Write the failing test for the callback-shim wrapping**

Append to `tests/test_driver_manager.py`:

```python
def test_wrap_async_js_wraps_body_with_callback():
    mgr = SeleniumDriverManager()
    body = "(async () => { return JSON.stringify({a:1}); })()"
    wrapped = mgr._wrap_async_js(body)
    assert "var cb = arguments[arguments.length - 1];" in wrapped
    assert body in wrapped
    assert ".catch(e => cb(JSON.stringify({error: e.message})))" in wrapped


def test_wrap_async_js_handles_empty_body():
    mgr = SeleniumDriverManager()
    wrapped = mgr._wrap_async_js("")
    assert "cb(JSON.stringify(" in wrapped
```

- [ ] **Step 7: Run test to verify it fails**

Run: `pytest tests/test_driver_manager.py::test_wrap_async_js_wraps_body_with_callback -v`
Expected: FAIL with `AttributeError: 'SeleniumDriverManager' object has no attribute '_wrap_async_js'`

- [ ] **Step 8: Implement _wrap_async_js**

Add this method to `SeleniumDriverManager` in `driver.py` (after `__init__`):

```python
    def _wrap_async_js(self, js_body: str) -> str:
        """Wrap an async JS body in the execute_async_script callback shim."""
        return (
            "var cb = arguments[arguments.length - 1];\n"
            "(async () => {\n"
            f"{js_body}\n"
            "})().catch(e => cb(JSON.stringify({error: e.message})));"
        )
```

- [ ] **Step 9: Run test to verify it passes**

Run: `pytest tests/test_driver_manager.py -v`
Expected: PASS (all 4 tests)

- [ ] **Step 10: Commit**

```bash
git add driver.py tests/test_driver_manager.py
git commit -m "feat: callback-shim wrapping for execute_async_script"
```

- [ ] **Step 11: Write the failing test for execute_async_js**

Append to `tests/test_driver_manager.py`:

```python
def test_execute_async_js_calls_execute_async_script_and_returns_string():
    mgr = SeleniumDriverManager()
    fake_driver = MagicMock()
    fake_driver.execute_async_script.return_value = '{"result": "ok"}'
    mgr._driver = fake_driver
    out = mgr.execute_async_js("some js here", timeout=45)
    fake_driver.set_script_timeout.assert_called_once_with(45)
    fake_driver.execute_async_script.assert_called_once()
    called_arg = fake_driver.execute_async_script.call_args[0][0]
    assert "var cb = arguments[arguments.length - 1];" in called_arg
    assert "some js here" in called_arg
    assert out == '{"result": "ok"}'


def test_execute_async_js_returns_error_string_on_js_error():
    mgr = SeleniumDriverManager()
    fake_driver = MagicMock()
    fake_driver.execute_async_script.return_value = '{"error": "boom"}'
    mgr._driver = fake_driver
    out = mgr.execute_async_js("js", timeout=60)
    assert out == '{"error": "boom"}'
```

- [ ] **Step 12: Run test to verify it fails**

Run: `pytest tests/test_driver_manager.py::test_execute_async_js_calls_execute_async_script_and_returns_string -v`
Expected: FAIL with `AttributeError: ... has no attribute 'execute_async_js'`

- [ ] **Step 13: Implement execute_async_js**

Add to `driver.py` (after `_wrap_async_js`):

```python
    def execute_async_js(self, js_body: str, timeout: int = 60) -> str:
        """Execute an async JS body via execute_async_script. Returns raw string."""
        if self._driver is None:
            raise RuntimeError("Driver not connected. Call connect() first.")
        self._driver.set_script_timeout(timeout)
        wrapped = self._wrap_async_js(js_body)
        return self._driver.execute_async_script(wrapped)
```

- [ ] **Step 14: Run test to verify it passes**

Run: `pytest tests/test_driver_manager.py -v`
Expected: PASS (all 6 tests)

- [ ] **Step 15: Commit**

```bash
git add driver.py tests/test_driver_manager.py
git commit -m "feat: execute_async_js wraps execute_async_script"
```

- [ ] **Step 16: Write the failing test for connect()**

Append to `tests/test_driver_manager.py`:

```python
@patch("driver.uc")
def test_connect_creates_driver_with_profile_and_version(mock_uc):
    mgr = SeleniumDriverManager(profile_dir="/tmp/fake-profile", version_main=148)
    fake_driver = MagicMock()
    mock_uc.Chrome.return_value = fake_driver
    result = mgr.connect()
    assert result is fake_driver
    mock_uc.Chrome.assert_called_once()
    kwargs = mock_uc.Chrome.call_args.kwargs
    assert kwargs["version_main"] == 148
    options = kwargs["options"]
    assert options.user_data_dir == "/tmp/fake-profile"


@patch("driver.uc")
def test_connect_reuses_existing_driver(mock_uc):
    mgr = SeleniumDriverManager()
    fake_driver = MagicMock()
    mgr._driver = fake_driver
    result = mgr.connect()
    assert result is fake_driver
    mock_uc.Chrome.assert_not_called()
```

- [ ] **Step 17: Run test to verify it fails**

Run: `pytest tests/test_driver_manager.py::test_connect_creates_driver_with_profile_and_version -v`
Expected: FAIL — `connect` doesn't exist, and `driver.uc` import fails.

- [ ] **Step 18: Implement connect() and the uc import**

At the top of `driver.py`, add the import (after `import os`):

```python
import undetected_chromedriver as uc
```

Add the `connect` method to `SeleniumDriverManager` (after `execute_async_js`):

```python
    def connect(self):
        """Return the existing driver if alive, else launch a new one."""
        if self._driver is not None:
            try:
                _ = self._driver.current_url
                return self._driver
            except Exception:
                self._driver = None
        options = uc.ChromeOptions()
        options.user_data_dir = self.profile_dir
        self._driver = uc.Chrome(options=options, version_main=self.version_main)
        return self._driver
```

Note: the `try: _ = self._driver.current_url` check detects a dead driver without raising — if accessing `current_url` fails, the driver process has died and we relaunch.

- [ ] **Step 19: Run test to verify it passes**

Run: `pytest tests/test_driver_manager.py -v`
Expected: PASS (all 8 tests)

- [ ] **Step 20: Commit**

```bash
git add driver.py tests/test_driver_manager.py
git commit -m "feat: connect() launches persistent undetected-chromedriver"
```

- [ ] **Step 21: Write the failing test for is_logged_in / ensure_logged_in**

Append to `tests/test_driver_manager.py`:

```python
def test_is_logged_in_true_when_no_login_button():
    mgr = SeleniumDriverManager()
    fake_driver = MagicMock()
    fake_driver.current_url = "https://www.udemy.com/course/xyz/learn"
    fake_driver.execute_script.return_value = 0
    mgr._driver = fake_driver
    assert mgr.is_logged_in() is True


def test_is_logged_in_false_when_login_url():
    mgr = SeleniumDriverManager()
    fake_driver = MagicMock()
    fake_driver.current_url = "https://www.udemy.com/join/login"
    mgr._driver = fake_driver
    assert mgr.is_logged_in() is False


def test_is_logged_in_false_when_signin_button_present():
    mgr = SeleniumDriverManager()
    fake_driver = MagicMock()
    fake_driver.current_url = "https://www.udemy.com/course/xyz/learn"
    fake_driver.execute_script.return_value = 1
    mgr._driver = fake_driver
    assert mgr.is_logged_in() is False


def test_ensure_logged_in_raises_when_not_logged_in():
    mgr = SeleniumDriverManager()
    fake_driver = MagicMock()
    fake_driver.current_url = "https://www.udemy.com/join/login"
    mgr._driver = fake_driver
    import pytest
    with pytest.raises(RuntimeError, match="Not logged in"):
        mgr.ensure_logged_in()


def test_ensure_logged_in_passes_when_logged_in():
    mgr = SeleniumDriverManager()
    fake_driver = MagicMock()
    fake_driver.current_url = "https://www.udemy.com/course/xyz/learn"
    fake_driver.execute_script.return_value = 0
    mgr._driver = fake_driver
    mgr.ensure_logged_in()
```

- [ ] **Step 22: Run test to verify it fails**

Run: `pytest tests/test_driver_manager.py::test_is_logged_in_true_when_no_login_button -v`
Expected: FAIL with `AttributeError: ... has no attribute 'is_logged_in'`

- [ ] **Step 23: Implement is_logged_in and ensure_logged_in**

Add to `driver.py` (after `connect`):

```python
    def is_logged_in(self) -> bool:
        """Check whether the current page is past Udemy's login wall."""
        if self._driver is None:
            return False
        url = self._driver.current_url or ""
        if "join/login" in url or "join/signup" in url:
            return False
        sign_in_count = self._driver.execute_script(
            "return document.querySelectorAll('a[href*=\"join/login\"], "
            "button[data-purpose*=\"sign-in\"], a[data-purpose*=\"sign-in\"]').length;"
        )
        return sign_in_count == 0

    def ensure_logged_in(self) -> None:
        """Raise RuntimeError if not logged in."""
        if not self.is_logged_in():
            raise RuntimeError(
                "Not logged in. Launch the scraper, log into Udemy once in "
                "the Selenium window, then retry."
            )
```

- [ ] **Step 24: Run test to verify it passes**

Run: `pytest tests/test_driver_manager.py -v`
Expected: PASS (all 13 tests)

- [ ] **Step 25: Commit**

```bash
git add driver.py tests/test_driver_manager.py
git commit -m "feat: login-wall detection and ensure_logged_in"
```

- [ ] **Step 26: Write the failing test for reconnect and quit**

Append to `tests/test_driver_manager.py`:

```python
@patch("driver.uc")
def test_reconnect_quits_old_and_relaunches(mock_uc):
    mgr = SeleniumDriverManager(profile_dir="/tmp/fake-profile")
    old_driver = MagicMock()
    mgr._driver = old_driver
    new_driver = MagicMock()
    mock_uc.Chrome.return_value = new_driver
    result = mgr.reconnect()
    old_driver.quit.assert_called_once()
    assert result is new_driver


@patch("driver.uc")
def test_reconnect_guarded_against_loops(mock_uc):
    mgr = SeleniumDriverManager()
    mgr._reconnecting = True
    old_driver = MagicMock()
    mgr._driver = old_driver
    mock_uc.Chrome.return_value = MagicMock()
    result = mgr.reconnect()
    assert result is old_driver
    mock_uc.Chrome.assert_not_called()


def test_quit_calls_driver_quit_and_clears():
    mgr = SeleniumDriverManager()
    fake_driver = MagicMock()
    mgr._driver = fake_driver
    mgr.quit()
    fake_driver.quit.assert_called_once()
    assert mgr._driver is None


def test_quit_when_no_driver_is_noop():
    mgr = SeleniumDriverManager()
    mgr.quit()
    assert mgr._driver is None
```

- [ ] **Step 27: Run test to verify it fails**

Run: `pytest tests/test_driver_manager.py::test_reconnect_quits_old_and_relaunches -v`
Expected: FAIL with `AttributeError: ... has no attribute 'reconnect'`

- [ ] **Step 28: Implement reconnect and quit**

Add to `driver.py` (after `ensure_logged_in`):

```python
    def reconnect(self):
        """Quit the dead driver and relaunch on the same profile. One-shot guard."""
        if self._reconnecting:
            return self._driver
        self._reconnecting = True
        try:
            if self._driver is not None:
                try:
                    self._driver.quit()
                except Exception:
                    pass
                self._driver = None
            options = uc.ChromeOptions()
            options.user_data_dir = self.profile_dir
            self._driver = uc.Chrome(options=options, version_main=self.version_main)
            return self._driver
        finally:
            self._reconnecting = False

    def quit(self):
        """Quit the driver and clear the reference."""
        if self._driver is not None:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None
```

- [ ] **Step 29: Run test to verify it passes**

Run: `pytest tests/test_driver_manager.py -v`
Expected: PASS (all 17 tests)

- [ ] **Step 30: Commit**

```bash
git add driver.py tests/test_driver_manager.py
git commit -m "feat: reconnect with loop guard and quit cleanup"
```

- [ ] **Step 31: Add the module-level shared instance**

At the bottom of `driver.py`, add:

```python
# Single shared instance — survives across ScraperSession instances
# so the driver stays alive on the login-wall path (first-run setup).
shared_manager = SeleniumDriverManager()
```

No test needed for this line — it's a module-level singleton, exercised by the session tests in Task 6.

- [ ] **Step 32: Commit**

```bash
git add driver.py
git commit -m "feat: shared SeleniumDriverManager singleton"
```

---

### Task 4: Rewrite scraper.py — route JS through SeleniumDriverManager

**Files:**
- Modify: `scraper.py` (full rewrite of internals; keep public method names)
- Test: `tests/test_driver_manager.py` (already passes — no new tests here, scraper tests come in Task 6)

This task replaces the browser-use subprocess layer in `scraper.py` with calls to `driver.shared_manager`. The in-page JS bodies (course discovery, batch transcript fetch, VTT parsing) are preserved from the existing file.

- [ ] **Step 1: Read the current scraper.py to preserve the JS bodies**

Run: `cat scraper.py` (or use the Read tool). Note the exact JS strings in `discover_course()` (lines 86-139) and `fetch_transcripts_batch()` (lines 156-222) — these will be reused verbatim. Also note `extract_course_slug`, `sanitize_filename`, `save_transcript` — these are pure Python and stay unchanged.

- [ ] **Step 2: Rewrite scraper.py — imports and class header**

Replace the top of `scraper.py` (lines 1-24, the docstring + `run_browser_use`) with:

```python
"""
Udemy Transcript Scraper Engine
Uses undetected-chromedriver to extract transcripts from Udemy courses
via the browser's authenticated session. Sequential batch processing with
a single persistent driver.
"""
import json
import os
import re
import time

from driver import shared_manager
from selenium.common.exceptions import (
    TimeoutException,
    JavascriptException,
    WebDriverException,
    InvalidSessionIdException,
)
```

Keep `extract_course_slug` and `sanitize_filename` unchanged (lines 27-45 of the original).

- [ ] **Step 3: Rewrite the UdemyScraper class — __init__ and connect_and_navigate**

Replace the `__init__` and `connect_and_navigate` methods with:

```python
class UdemyScraper:
    """Scrapes transcripts from a Udemy course using undetected-chromedriver."""

    def __init__(self, log_callback=None):
        self.log = log_callback or (lambda msg: print(msg))
        self.course_id = None
        self.course_title = None
        self.course_slug = None
        self.sections = []
        self.output_dir = None
        self.driver = None

    def connect_and_navigate(self, url: str) -> bool:
        """Connect to the persistent driver and navigate to the course page."""
        self.course_slug = extract_course_slug(url)
        self.log(f"Course slug: {self.course_slug}")

        self.driver = shared_manager.connect()
        course_url = f"https://www.udemy.com/course/{self.course_slug}/learn"
        self.driver.get(course_url)
        time.sleep(2)

        shared_manager.ensure_logged_in()
        self.log("Connected and navigated to course page.")
        return True
```

- [ ] **Step 4: Rewrite _js and _js_json**

Replace the old `_js`/`_js_json` methods with:

```python
    def _js(self, js_body: str, timeout: int = 30) -> str:
        """Execute an async JS body in the browser and return the result string."""
        return shared_manager.execute_async_js(js_body, timeout=timeout)

    def _js_json(self, js_body: str, timeout: int = 30):
        """Execute an async JS body and parse the result as JSON."""
        raw = self._js(js_body, timeout)
        return json.loads(raw)
```

- [ ] **Step 5: Keep discover_course, create_folder_structure, save_transcript unchanged**

`discover_course()` (original lines 86-139), `create_folder_structure()` (141-154), and `save_transcript()` (224-236) use `_js_json` / `_js` and pure Python. They work as-is now that `_js` routes through `shared_manager.execute_async_js`. Do not change them.

Copy these three methods verbatim from the original into the new file.

- [ ] **Step 6: Rewrite fetch_transcripts_batch with the reworked fallback**

Replace the `fetch_transcripts_batch` method with:

```python
    def fetch_transcripts_batch(self, lecture_ids: list, retries=2) -> dict:
        """
        Fetch transcripts for multiple lectures in parallel within a single
        async JS call. Returns dict: {lecture_id: {s: status, t: transcript}}.
        On driver-level failures, split the batch and retry recursively.
        """
        if not lecture_ids:
            return {}

        ids_json = json.dumps(lecture_ids)
        code = f"""
    const ids = {ids_json};
    const results = {{}};

    async function fetchOne(id) {{
        try {{
            const resp = await fetch('/api-2.0/users/me/subscribed-courses/{self.course_id}/lectures/' + id + '/?fields[lecture]=asset&fields[asset]=captions');
            if (!resp.ok) {{ results[id] = {{s: 'api_error'}}; return; }}

            const data = await resp.json();
            if (!data.asset || !data.asset.captions || data.asset.captions.length === 0) {{
                results[id] = {{s: 'no_captions'}}; return;
            }}

            let cap = data.asset.captions.find(c => c.locale_id.startsWith('en_') && c.source === 'manual');
            if (!cap) cap = data.asset.captions.find(c => c.locale_id.startsWith('en_'));
            if (!cap) {{ results[id] = {{s: 'no_english'}}; return; }}

            const vttResp = await fetch(cap.url);
            if (!vttResp.ok) {{ results[id] = {{s: 'vtt_error'}}; return; }}

            const vttText = await vttResp.text();
            const lines = vttText.split(String.fromCharCode(10));
            const textParts = [];
            const numRegex = new RegExp("^\\\\d+$");
            const htmlRegex = new RegExp("<[^>]+>", "g");
            for (const line of lines) {{
                const t = line.trim();
                if (!t || t.includes('-->') || t === 'WEBVTT' || t.startsWith('Kind:') || t.startsWith('Language:')) continue;
                if (numRegex.test(t)) continue;
                const clean = t.replace(htmlRegex, '');
                if (clean) textParts.push(clean);
            }}
            const transcript = textParts.join(' ');
            if (!transcript) {{ results[id] = {{s: 'empty'}}; return; }}
            results[id] = {{s: 'ok', t: transcript, lang: cap.locale_id}};
        }} catch(e) {{
            results[id] = {{s: 'error', m: e.message.substring(0, 100)}};
        }}
    }}

    await Promise.all(ids.map(id => fetchOne(id)));
    return JSON.stringify(results);
"""
        try:
            raw = self._js(code, timeout=60)
            return json.loads(raw)
        except (TimeoutException, JavascriptException, WebDriverException) as e:
            self.log(f"  Batch driver error ({type(e).__name__}): {str(e)[:80]}")
            if retries > 0 and len(lecture_ids) > 1:
                time.sleep(0.5)
                mid = len(lecture_ids) // 2
                left = self.fetch_transcripts_batch(lecture_ids[:mid], retries - 1)
                right = self.fetch_transcripts_batch(lecture_ids[mid:], retries - 1)
                left.update(right)
                return left
            return {lid: {"s": "error"} for lid in lecture_ids}
```

Note: the JS body is the same as the original but is no longer wrapped in `(async () => {...})()` — the `_wrap_async_js` shim in `driver.py` does that wrapping now. The JS body is just the inner statements (declare `ids`, `results`, `fetchOne`, `await Promise.all`, `return JSON.stringify`).

- [ ] **Step 7: Rewrite scrape_parallel as sequential**

Replace `scrape_parallel` with:

```python
    def scrape_parallel(self, base_dir: str, progress_callback=None, stop_check=None,
                        batch_size=40, num_threads=3, skip_discovery=False):
        """
        Sequential batch scraping with a single persistent driver.
        (Name kept for backward compat; num_threads is ignored.)
        """
        if not skip_discovery:
            self.discover_course()
            self.create_folder_structure(base_dir)
        else:
            self.log(f"Using pre-discovered course: {self.course_title}")

        all_lectures = []
        for si, section in enumerate(self.sections):
            for li, lecture in enumerate(section["lectures"]):
                all_lectures.append((si, li, lecture))

        self.log(f"Processing {len(all_lectures)} lectures in batches of {batch_size}")

        completed = 0
        failed = 0

        for start in range(0, len(all_lectures), batch_size):
            if stop_check and stop_check():
                self.log("Stop requested. Halting.")
                break

            batch = all_lectures[start:start + batch_size]
            batch_ids = [item[2]["id"] for item in batch]

            if progress_callback:
                for si, li, lec in batch:
                    progress_callback({
                        "type": "lecture_status",
                        "sectionIdx": si, "lectureIdx": li,
                        "status": "working",
                        "message": f"{lec['title'][:40]}",
                    })

            try:
                results = self.fetch_transcripts_batch(batch_ids)
            except Exception as e:
                self.log(f"Batch error: {e}")
                results = {lid: {"s": "error"} for lid in batch_ids}

            for si, li, lec in batch:
                result = results.get(lec["id"], {"s": "error"})
                status = result.get("s", "error")
                transcript = result.get("t", "")

                if status == "ok" and transcript:
                    self.save_transcript(self.sections[si], lec, li + 1, transcript)
                    completed += 1
                    self.log(f"  Saved: {lec['title'][:50]} ({len(transcript)} chars)")
                    if progress_callback:
                        progress_callback({
                            "type": "lecture_status", "sectionIdx": si, "lectureIdx": li,
                            "status": "saved", "message": f"Saved: {lec['title'][:50]}",
                            "size": len(transcript),
                        })
                elif status in ("no_captions", "no_english", "api_error"):
                    completed += 1
                    if progress_callback:
                        progress_callback({
                            "type": "lecture_status", "sectionIdx": si, "lectureIdx": li,
                            "status": "skipped", "message": f"Skipped ({status})",
                        })
                else:
                    failed += 1
                    self.log(f"  Failed ({status}): {lec['title'][:50]}")
                    if progress_callback:
                        progress_callback({
                            "type": "lecture_status", "sectionIdx": si, "lectureIdx": li,
                            "status": "failed", "message": f"Failed ({status})",
                        })

            time.sleep(0.3)

        self.log(f"\nDone! {completed} completed, {failed} failed.")
        if progress_callback:
            progress_callback({"type": "scrape_finished", "completed": completed, "failed": failed})
```

- [ ] **Step 8: Verify scraper.py imports cleanly**

Run:
```bash
python -c "from scraper import UdemyScraper, extract_course_slug, sanitize_filename; print('ok')"
```

Expected: `ok`. If there's a syntax error or missing import, fix it.

- [ ] **Step 9: Verify existing extract_slug tests still pass**

Run: `pytest tests/test_extract_slug.py -v`
Expected: PASS (unchanged tests)

- [ ] **Step 10: Commit**

```bash
git add scraper.py
git commit -m "feat: rebuild scraper.py on SeleniumDriverManager"
```

---

### Task 5: Refactor backend/scraper_session.py — single-driver sequential model

**Files:**
- Modify: `backend/scraper_session.py`
- Test: `tests/test_scraper_session.py` (update in Task 6)

The session drops its multi-threaded worker model. `_run()` becomes a sequential loop. `retry_failed()` uses the shared driver. Stop/shutdown clean up the driver.

- [ ] **Step 1: Read the current scraper_session.py**

Run: `cat backend/scraper_session.py` (or use the Read tool). Note the structure: `__init__`, `_emit`, `_on_scraper_event`, `_on_log`, `_counts`, `_emit_progress`, `_run`, `start`, `stop`, `retry_failed`, `_run_retry`, `events`.

- [ ] **Step 2: Add driver import to scraper_session.py**

At the top of `backend/scraper_session.py`, add after the existing imports:

```python
from driver import shared_manager
```

- [ ] **Step 3: Rewrite _run() — sequential, no worker threads**

Replace the `_run` method (original lines 85-145) with:

```python
    def _run(self, url: str, output_dir: str, batch_size: int, num_threads: int, resume: bool):
        output_dir = os.path.expanduser(output_dir)
        try:
            self.scraper = UdemyScraper(log_callback=self._on_log)
            self.tracker = ProgressTracker(output_dir)

            if resume:
                slug = self.tracker.state.get("course_slug")
                if slug:
                    url = f"https://www.udemy.com/course/{slug}/learn"
                    self._emit({"type": "log", "message": f"Resumed course: {slug}", "level": "info"})

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

            if num_threads and num_threads != 1:
                self._emit({"type": "log",
                            "message": f"num_threads={num_threads} ignored (single-driver sequential mode)",
                            "level": "info"})

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
```

Key changes from the original: no `queue.Queue`, no `worker()` function, no `threading.Thread` launch loop, no staggered starts. The call to `scrape_parallel` is the same (it's now sequential internally). A log line notes `num_threads` is ignored.

- [ ] **Step 4: Update retry_failed and _run_retry to use the shared driver**

Replace `retry_failed` and `_run_retry` (original lines 161-205) with:

```python
    def retry_failed(self):
        to_retry = []
        for (si, li), st in list(self.lecture_states.items()):
            if st == "failed":
                lec = self.scraper.sections[si]["lectures"][li]
                to_retry.append((si, li, lec["id"], lec["title"]))
                self.lecture_states[(si, li)] = "in-progress"
                self._emit({"type": "lecture_status", "sectionIdx": si, "lectureIdx": li,
                            "status": "in-progress", "message": "Retrying", "size": None})
        if not to_retry:
            return
        self._emit_progress()
        thread = threading.Thread(target=self._run_retry, args=(to_retry,), daemon=True)
        thread.start()

    def _run_retry(self, to_retry: list):
        try:
            scraper = self.scraper
            batch_ids = [item[2] for item in to_retry]
            results = scraper.fetch_transcripts_batch(batch_ids)
            for si, li, lid, title in to_retry:
                result = results.get(lid, {"s": "error"})
                status = result.get("s", "error")
                transcript = result.get("t", "")
                if status == "ok" and transcript:
                    scraper.save_transcript(self.scraper.sections[si],
                                            self.scraper.sections[si]["lectures"][li],
                                            li + 1, transcript)
                    self.lecture_states[(si, li)] = "success"
                    self._emit({"type": "lecture_status", "sectionIdx": si, "lectureIdx": li,
                                "status": "success", "message": f"Retry OK: {title[:40]}",
                                "size": len(transcript)})
                elif status in ("no_captions", "no_english", "api_error"):
                    self.lecture_states[(si, li)] = "skipped"
                    self._emit({"type": "lecture_status", "sectionIdx": si, "lectureIdx": li,
                                "status": "skipped", "message": f"Retry skipped ({status})"})
                else:
                    self.lecture_states[(si, li)] = "failed"
                    self._emit({"type": "lecture_status", "sectionIdx": si, "lectureIdx": li,
                                "status": "failed", "message": f"Retry failed ({status})"})
            self._emit_progress()
        except Exception as e:
            self._emit({"type": "error", "message": f"Retry error: {e}"})
```

Key change: `_run_retry` no longer creates a new `UdemyScraper` — it reuses `self.scraper` (which holds the shared driver).

- [ ] **Step 5: Update stop() to clean up the driver**

Replace the `stop` method (original line 158-159) with:

```python
    def stop(self):
        self.stop_flag = True
```

The driver cleanup happens in `server.py`'s `shutdown_session` (Task 7). No driver quit here — `stop_flag` causes the scrape loop to halt between batches, and `_run`'s `finally` block resets state.

- [ ] **Step 6: Verify scraper_session imports cleanly**

Run:
```bash
python -c "from backend.scraper_session import ScraperSession, normalize_status; print('ok')"
```

Expected: `ok`

- [ ] **Step 7: Commit**

```bash
git add backend/scraper_session.py
git commit -m "refactor: single-driver sequential session model"
```

---

### Task 6: Update tests/test_scraper_session.py

**Files:**
- Modify: `tests/test_scraper_session.py`

The existing tests (`test_normalize_status_mapping`, `test_session_wraps_callback_and_normalizes`) don't touch browser-use — they test the event-wrapping logic, which is unchanged. They should still pass. We add new tests for the sequential loop, stop_flag, retry, and login-wall paths — all with a mocked `shared_manager`.

- [ ] **Step 1: Confirm existing tests still pass**

Run: `pytest tests/test_scraper_session.py -v`
Expected: PASS (both existing tests). If they fail, the scraper_session.py rewrite broke something — fix before adding new tests.

- [ ] **Step 2: Write the test for the sequential batch loop with a fake shared_manager**

Append to `tests/test_scraper_session.py`:

```python
from unittest.mock import patch, MagicMock
from scraper import UdemyScraper


def _make_scraper_with_sections():
    s = UdemyScraper(log_callback=lambda m: None)
    s.course_id = 123
    s.course_title = "Test Course"
    s.output_dir = "/tmp/out"
    s.sections = [
        {"index": 1, "id": "sec1", "title": "S1", "folder_name": "01_S1",
         "lectures": [{"id": "l1", "title": "L1"}, {"id": "l2", "title": "L2"}]}
    ]
    return s


@patch("scraper.shared_manager")
def test_sequential_batch_loop_emits_saved_events(mock_mgr):
    mock_mgr.execute_async_js.return_value = '{"l1": {"s":"ok","t":"hello world"}, "l2": {"s":"ok","t":"foo bar"}}'

    s = _make_scraper_with_sections()
    s.save_transcript = MagicMock(return_value="/tmp/fake")

    events = []
    s.scrape_parallel(
        base_dir="/tmp/out",
        progress_callback=lambda ev: events.append(ev),
        stop_check=lambda: False,
        batch_size=10,
        skip_discovery=True,
    )

    statuses = [e["status"] for e in events if e.get("type") == "lecture_status"]
    assert "working" in statuses
    assert statuses.count("saved") == 2
    finished = [e for e in events if e.get("type") == "scrape_finished"]
    assert len(finished) == 1
    assert finished[0]["completed"] == 2
    assert finished[0]["failed"] == 0


@patch("scraper.shared_manager")
def test_stop_check_halts_loop_mid_batch(mock_mgr):
    mock_mgr.execute_async_js.return_value = '{}'

    s = _make_scraper_with_sections()
    events = []
    call_count = [0]
    def stop_check():
        call_count[0] += 1
        return call_count[0] > 1

    s.scrape_parallel(
        base_dir="/tmp/out",
        progress_callback=lambda ev: events.append(ev),
        stop_check=stop_check,
        batch_size=1,
        skip_discovery=True,
    )

    finished = [e for e in events if e.get("type") == "scrape_finished"]
    assert len(finished) == 1
```

- [ ] **Step 3: Write the test for skipped and failed statuses**

Append:

```python
@patch("scraper.shared_manager")
def test_no_captions_status_is_skipped(mock_mgr):
    mock_mgr.execute_async_js.return_value = '{"l1": {"s":"no_captions"}, "l2": {"s":"error"}}'

    s = _make_scraper_with_sections()

    events = []
    s.scrape_parallel(
        base_dir="/tmp/out",
        progress_callback=lambda ev: events.append(ev),
        stop_check=lambda: False,
        batch_size=10,
        skip_discovery=True,
    )

    statuses = [e["status"] for e in events if e.get("type") == "lecture_status"]
    assert "skipped" in statuses
    assert "failed" in statuses
    finished = [e for e in events if e.get("type") == "scrape_finished"]
    assert finished[0]["completed"] == 1
    assert finished[0]["failed"] == 1
```

- [ ] **Step 4: Run all session tests**

Run: `pytest tests/test_scraper_session.py -v`
Expected: PASS (all tests — 2 original + 3 new). These tests exercise `scrape_parallel` which was implemented in Task 4, so they should pass immediately. If a test fails, read the error — most likely the mock isn't intercepting correctly; verify `@patch("scraper.shared_manager")` targets the import in `scraper.py`.

- [ ] **Step 5: Commit**

```bash
git add tests/test_scraper_session.py
git commit -m "test: sequential batch loop, stop_check, skip/fail statuses"
```

---

### Task 7: Add driver cleanup to server.py shutdown

**Files:**
- Modify: `backend/server.py`

The spec requires `shutdown_session()` to quit any live driver. Currently `shutdown_session` only calls `session.stop()`. We add a `shared_manager.quit()` call.

- [ ] **Step 1: Read the current server.py shutdown function**

The `shutdown_session` function (lines 101-103) calls `app.state.session.stop()` if running. We add driver cleanup after it.

- [ ] **Step 2: Add the import and quit call**

At the top of `backend/server.py`, add:

```python
from driver import shared_manager
```

Replace the `shutdown_session` function with:

```python
def shutdown_session():
    if app.state.session and getattr(app.state.session, "is_running", False):
        app.state.session.stop()
    shared_manager.quit()
```

- [ ] **Step 3: Verify server imports cleanly**

Run:
```bash
python -c "from backend.server import app, shutdown_session; print('ok')"
```

Expected: `ok`

- [ ] **Step 4: Run server tests to confirm no regression**

Run: `pytest tests/test_server.py -v`
Expected: PASS (all 5 tests). The `FakeSession` in `test_server.py` doesn't use the real driver, and `shared_manager.quit()` is safe when no driver exists (it's a no-op — see `test_quit_when_no_driver_is_noop` from Task 3).

- [ ] **Step 5: Commit**

```bash
git add backend/server.py
git commit -m "feat: quit shared driver on server shutdown"
```

---

### Task 8: Full test suite + import verification

**Files:**
- (no changes — verification only)

- [ ] **Step 1: Run the full test suite**

Run:
```bash
pytest tests/ -v
```

Expected: all tests PASS. Count: 17 driver-manager tests + 5 session tests + 5 server tests + extract_slug tests + progress_tracker tests. Any failure must be fixed before proceeding.

- [ ] **Step 2: Verify all modules import together**

Run:
```bash
python -c "from scraper import UdemyScraper; from driver import shared_manager, SeleniumDriverManager; from backend.scraper_session import ScraperSession; from backend.server import app, shutdown_session; print('all imports ok')"
```

Expected: `all imports ok`

- [ ] **Step 3: Verify no browser-use references remain**

Run:
```bash
grep -rn "browser.use\|run_browser_use\|browser_use" --include="*.py" .
```

Expected: no matches (the `.git`, `venv`, `__pycache__` dirs may have stale refs — ignore those; only check source files). If any source file still references browser-use, remove it.

- [ ] **Step 4: Commit if any cleanup was needed**

If Step 3 found and removed references:
```bash
git add -A && git commit -m "cleanup: remove residual browser-use references"
```
Otherwise skip this step.

---

### Task 9: Manual integration test (not CI)

**Files:**
- (no code changes — manual verification)

This task verifies the full pipeline against a real Udemy course. It cannot be automated in CI because it requires the user's logged-in session and a real Chrome launch.

- [ ] **Step 1: First-run login setup**

Run the app:
```bash
./run.sh
```

If this is the first ever run (no `~/.udemy-scraper-profile` exists), the Selenium Chrome window opens with a fresh profile. Navigate to `https://www.udemy.com/join/login` in that window and log in manually. The error event "Not logged in" should appear in the UI — this is expected. After logging in, stop the app (Ctrl+C) and restart.

- [ ] **Step 2: Run against the test course**

Start the app again. In the UI, enter the URL:
```
https://www.udemy.com/course/recreate-stardew-valley-in-godot/learn
```
Set output dir to `/tmp/udemy-test`, batch size 10, click Start.

Expected: course discovered (6 sections, 32 lectures), transcripts begin saving. The "Not logged in" error should NOT appear this time.

- [ ] **Step 3: Verify transcripts on disk**

After completion, run:
```bash
ls /tmp/udemy-test/"Recreate Stardew Valley in Godot"/
```

Expected: 6 section folders. Each folder contains `.txt` files for lectures that had English captions. Lectures with `no_captions` show as "skipped" in the UI but produce no file.

- [ ] **Step 4: Verify transcript content**

Open one transcript file:
```bash
cat /tmp/udemy-test/"Recreate Stardew Valley in Godot"/01_*/*.txt | head -5
```

Expected: a header (`Lecture: ...`, `Section: ...`, `===`), then the transcript text. The text should be coherent English (auto-generated captions).

- [ ] **Step 5: Test stop mid-run**

Start a scrape, wait for a few lectures to save, then click Stop in the UI. Expected: the loop halts within one batch, "Stop requested. Halting." appears in the log, and the session ends cleanly. No orphaned Chrome processes (verify with `pgrep -fa chromedriver` after a few seconds).

- [ ] **Step 6: Test resume**

After stopping mid-run, click Resume (or Start with the same output dir). Expected: previously-saved lectures show as "Resumed" (success), the loop continues from where it stopped.

- [ ] **Step 7: Test retry-failed**

If any lectures failed, click "Retry Failed" in the UI. Expected: failed lectures re-fetch via the shared driver and update status.

- [ ] **Step 8: Commit any integration-test-triggered fixes**

If the integration test revealed bugs, fix them and commit:
```bash
git add -A && git commit -m "fix: <what the integration test caught>"
```

---

### Task 10: Final commit and branch summary

**Files:**
- (no code changes — finalization)

- [ ] **Step 1: Confirm all changes are committed**

Run: `git status`
Expected: `nothing to commit, working tree clean`

- [ ] **Step 2: Review the branch commit history**

Run: `git log --oneline main..feature/selenium-engine`
Expected: a series of commits from Tasks 2-7 (plus any fixes from Task 9).

- [ ] **Step 3: Confirm the test suite passes one final time**

Run: `pytest tests/ -v`
Expected: all PASS

- [ ] **Step 4: Leave the branch unmerged**

Do NOT merge to `main` unless the user explicitly asks. The spec says work stays on `feature/selenium-engine` so `main` is unaffected. Report completion to the user.
