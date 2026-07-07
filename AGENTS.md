# AGENTS.md

Development-workflow guide for the Udemy Transcript Scraper. Any agent
working in this repo reads this file top-to-bottom at the start of every
session, then follows the Standard Workflow below.

## Standard Workflow (read this first, every session)

1. **Orient.** Read this file top-to-bottom. Then `git log --oneline -15`
   and `git branch` to see recent work and the current branch.
2. **Find in-flight context.** If work is mid-flight, the context lives in
   `docs/superpowers/specs/` and `docs/superpowers/plans/` (newest by date),
   plus the commit history of the current feature branch — NOT in this
   file. Open the most recent spec + plan that match the current branch's
   purpose.
3. **One unit of work = one branch, never commit to `main`.**
   - New feature → branch as `feature/<short-slug>` off the latest `main`.
   - Bug fix (often found *after* a merge) → branch as `fix/<short-slug>`
     off the latest `main` (pull first: `git checkout main && git pull`).
     Never branch a fix off the old feature branch — it's already merged
     and may be deleted.
   See Branch Workflow for the full lifecycle.
4. **Design before code (non-trivial work).** Anything beyond a one-line
   fix goes through brainstorm → spec → plan → implement. Spec lands in
   `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`; plan in
   `docs/superpowers/plans/`. See Feature Process.
5. **Browser-use for the hard parts.** Use the browser-use skill manually
   for reverse-engineering, debugging scraper failures, multi-step
   digging, and new scraping targets. When you need speed or repetition,
   write a Python script that shells out to `browser-use` following the
   patterns in Browser-Use Scripted Patterns. Never re-derive the Udemy
   API shape from scratch — read `scraper.py` first.
6. **Run tests before handing off.** `source venv/bin/activate && pytest`
   from the repo root. Tests must pass before you ask the user to verify.
7. **Hand off for manual verification — this is the real merge gate.**
   Scripted tests are necessary but not sufficient. UI bugs, intermittent
   runtime errors, and edge cases surface only in the running app. Tell
   the user what to check; expect back-and-forth; a fix may need its own
   session. Never claim the work is "done" — say "ready for your
   verification."
8. **Merge only on explicit user approval.** Do not self-approve, do not
   auto-merge, do not open a PR unless asked. When the user says "merge
   it": `git checkout main && git merge --no-ff feature/<slug>`, then
   delete the feature branch. Same gate, same steps for `fix/<slug>`.
9. **Update context for the next session.** Commit the spec, the plan,
   and any debug notes/scratch files that would let a fresh agent pick up
   where you left off. This file stays static; the living state lives in
   specs, plans, and git.

## Project Overview

A desktop app that scrapes transcripts from Udemy courses the user is
enrolled in, using the user's already-logged-in Chrome session — no Udemy
credentials are handled by this app. The user pastes a course URL, picks
an output directory, and the app downloads English transcripts organized
by section/lecture into that directory.

### How it works

- `scraper.py` — the scraping engine. It shells out to `browser-use` (via
  subprocess) to run JavaScript inside the user's Chrome tab, hitting
  Udemy's own API/GraphQL endpoints from the authenticated session.
  Transcripts are fetched in parallel batches with a work queue and
  worker threads.
- `backend/server.py` — FastAPI app exposing `/api/start`, `/api/stop`,
  `/api/resume`, `/api/retry-failed`, plus a `/ws` WebSocket that streams
  per-lecture progress events to the UI.
- `backend/scraper_session.py` — adapter wrapping `UdemyScraper` into the
  event stream the server expects (normalizes statuses, owns the thread).
- `app.py` — launcher. Starts the FastAPI server on 127.0.0.1:8765, then
  opens a PyWebView window at that URL; falls back to the system browser if
  PyWebView/Qt/GTK isn't available.
- `frontend/` — vanilla JS/HTML/CSS (no build step). `app.js` holds
  `ScraperUI` (state + render + WS client + commands).
- `progress_tracker.py` — persists resume state to `scrape_state.json` in
  the output dir so an interrupted scrape can continue.

### Key constraint

Everything rides on the user's logged-in Udemy session in Chrome. If Chrome
isn't running, isn't logged in, or remote debugging isn't allowed,
`browser-use` can't connect and nothing works. See Browser-Use Manual
Playbook for diagnostics.

## Setup & Commands

### First-time setup
```bash
# Python 3.10+ expected.
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
# browser-use is invoked via subprocess — install it separately per its docs.
# It must be on PATH and able to attach to the running Chrome CDP endpoint.
```

### Run the app
```bash
./run.sh          # activates venv + runs app.py (opens PyWebView window)
# or manually:
source venv/bin/activate && python app.py
# Fallback if no GUI: server runs at http://127.0.0.1:8765/
```

### Tests
```bash
source venv/bin/activate && pytest          # from repo root
# Tests live in tests/ — pytest auto-discovers them. httpx is used for the
# server tests; no live Udemy or Chrome calls are made in the suite.
```

### Prerequisites before any scrape works
1. Chrome must be running.
2. Chrome remote debugging must be allowed (`chrome://inspect/#remote-debugging`
   → tick "Allow remote debugging for this browser instance").
3. The user must be logged into Udemy in that Chrome instance.
If any of these fail, run `browser-use --doctor` to diagnose before
blaming scraper code.

## Architecture Map

```
                       browser-use (subprocess, attaches to Chrome CDP)
                              │
                              ▼
   UdemyScraper (scraper.py)  ← runs JS via run_browser_use() / _js()
   ├─ connect_and_navigate()   opens a tab to the course's /learn page
   ├─ discover_course()        hits /api-2.0 + /api/2024-01/graphql
   ├─ fetch_transcripts_batch() parallel JS fetches of caption VTTs
   └─ save_transcript()        writes .txt to <output>/<section>/<lecture>.txt
          │
          ▼  (progress_callback emits event dicts)
   ScraperSession (backend/scraper_session.py)  ← adapter: owns the thread,
          │                                       normalizes statuses, queues
          │                                       events for the WS stream
          ▼
   FastAPI (backend/server.py)
   ├─ POST /api/start | /api/stop | /api/resume | /api/retry-failed
   └─ WS   /ws  →  pushes event dicts to the browser
          │
          ▼  (JSON over WebSocket)
   frontend/app.js (ScraperUI: state → render → DOM)
          │
          ▼
   PyWebView window (app.py)  — or system browser fallback

   progress_tracker.py  ←  ScraperSession writes/reads scrape_state.json
                            in the output dir for resume support.
```

### Three things that trip people up
- **`browser-use` is a subprocess call, not an imported library.**
  `run_browser_use()` wraps a heredoc in `subprocess.run` with a timeout.
  Don't try to import browser-use directly into scraper.py — the CDP daemon
  must own the connection.
- **The JS runs in the page's own context**, so it has the user's Udemy
  cookies. That's why fetches to `/api-2.0/...` just work — they're
  authenticated by the session. But it also means any change to Udemy's API
  shape breaks the JS, not the Python.
- **Progress is pushed, not polled.** `progress_callback` emits event dicts
  during `scrape_parallel()`; `ScraperSession` converts them to the WS feed.
  The frontend has no GET-status endpoint — it only reacts to WS events. If
  the UI looks stuck, check the WS connection in devtools, not an API call.

## Browser-Use Manual Playbook

Use browser-use directly in the session when search tools (Read/Grep/Glob)
can't reach the answer — i.e. anything that lives inside a logged-in web page,
behind an interaction, or in a network response. The four manual workflows:

### 1. Reverse-engineering site APIs / structure
Goal: figure out the shape of an endpoint before writing scraper code.
```bash
browser-use <<'PY'
new_tab("https://www.udemy.com/course/<slug>/learn")
wait_for_load()
print(page_info())
# Hit the endpoint from the page context so cookies apply:
result = js("""(async () => {
  const r = await fetch('/api-2.0/...');
  return JSON.stringify(await r.json());
})()""")
print(result)
PY
```
Always read `scraper.py` first — the Udemy API shape is already decoded there.
Don't re-derive `/api-2.0/users/me/subscribed-courses/<cid>/lectures/<lid>/`
or the GraphQL curriculum query from scratch.

### 2. Debugging scraper failures
Goal: find out *why* a lecture came back `no_captions` / `api_error` /
`vtt_error` / `no_english`.
```bash
browser-use <<'PY'
capture_screenshot()                      # am I still on a real Udemy tab?
ensure_real_tab()                         # not an omnibox popup
js("document.title")                      # did I get bounced to login?
# Replay the exact fetch scraper.py would do, but log the raw response:
js("""(async () => {
  const r = await fetch('/api-2.0/users/me/subscribed-courses/<CID>/lectures/<LID>/?fields[lecture]=asset&fields[asset]=captions');
  return JSON.stringify({status: r.status, body: await r.text()}, null, 2);
})()""")
PY
```
Common causes: session expired (redirect to /login), course not enrolled
(`403`), lecture is a quiz (no `asset.captions`), captions only in non-English
locales.

### 3. Multi-step interactive exploration
Goal: a value that only appears after several clicks/scrolls — nested menus,
lazy-loaded lists, "Show more" accordions.
```bash
browser-use <<'PY'
new_tab("https://www.udemy.com/...")
wait_for_load()
capture_screenshot()                      # see what's visible
click_at_xy(x, y)                         # from the screenshot's pixel coords
wait_for_load()
capture_screenshot()                      # verify the click worked
js("document.querySelector('...').textContent")   # extract the value
PY
```
Workflow: screenshot → read pixel → click → screenshot → extract. Never guess
a selector blind; verify visually first. If a click does nothing, the target
may be inside a shadow root or cross-origin iframe — check the
interaction-skills docs linked in the browser-use SKILL.md.

### 4. General scraping for new features
Goal: a different page/site entirely (reviews, instructor profile, etc.).
Same playbook: `new_tab` → `wait_for_load` → `screenshot` → `js(fetch...)`
or `js(querySelector...)`. Treat it as workflow #1 against a new host.

### When to stop and ask the user
- Any login wall, password, MFA, or consent prompt — stop. Exception: if Chrome
  is already signed in via SSO and no prompt appears, you may proceed.
- If `browser-use --doctor` can't connect after the user enables remote
  debugging, stop and report — don't loop retrying the daemon.
- If a page clearly says "access denied" / "you don't have this course" — the
  fix is on the user's account, not in code. Tell them.

## Browser-Use Scripted Patterns

When you need speed, repetition, or parallelism beyond manual session use,
write a Python script that shells out to `browser-use` — exactly as
`scraper.py` does. Codify the patterns below; don't reinvent them.

### Canonical shape (lifted from scraper.py)

```python
import subprocess, time

def run_browser_use(code: str, timeout: int = 30) -> str:
    """Run Python code in browser-use via a heredoc; return stdout."""
    wrapped = f"browser-use <<'BROWSER_USE_EOF'\n{code}\nBROWSER_USE_EOF"
    result = subprocess.run(
        ["bash", "-c", wrapped],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"browser-use failed: {result.stderr[:500]}")
    return result.stdout.strip()
```

### The four rules every script must follow

1. **Timeout every call.** `subprocess.run(..., timeout=...)` — never let a
   `browser-use` call hang. 30s default for inspection; 60s for batched fetches
   (matches `fetch_transcripts_batch`).
2. **Retry by splitting, not by re-running the whole batch.** On failure with a
   batch of N items, split in half, sleep briefly (0.5s), and recurse — up to a
   bounded depth. This localizes the failure instead of replaying N items that
   mostly succeeded. Copied verbatim from
   `scraper.py:fetch_transcripts_batch`:
   ```python
   except Exception as e:
       if retries > 0 and len(lecture_ids) > 1:
           mid = len(lecture_ids) // 2
           time.sleep(0.5)
           left  = self.fetch_transcripts_batch(lecture_ids[:mid], retries - 1)
           right = self.fetch_transcripts_batch(lecture_ids[mid:], retries - 1)
           left.update(right)
           return left
       raise
   ```
3. **Cap parallelism; stagger worker starts.** Multi-threaded scripts use a
   `queue.Queue` work queue and a small number of worker threads (3 default).
   Workers start 0.5s apart to avoid a CDP stampede. See
   `scraper.py:scrape_parallel`.
4. **Classify every result; never lose one.** Each fetch returns a status dict
   `{s: 'ok' | 'no_captions' | 'no_english' | 'api_error' | 'vtt_error' |
   'empty' | 'error', t: <transcript>, m: <err msg>}`. The caller switches on
   `s` to decide saved/skipped/failed — no silent drops. Copy this status
   vocabulary for any new script.

### When to write a script vs. drive browser-use manually

| Need                                  | Use            |
|---------------------------------------|----------------|
| One-off inspection, 1 fetch           | manual (heredoc)|
| "What does the API look like?"        | manual         |
| Fetch transcripts for N lectures      | script (this)  |
| Scrape a new page type at scale       | script (this)  |
| Debug why one lecture failed          | manual         |
| Anything interactive (clicks, scroll) | manual         |

### Don't

- Don't import browser-use as a Python library — the CDP daemon owns the
  connection. Always go through `subprocess` + heredoc.
- Don't retry by looping the same full batch — split instead (rule 2).
- Don't omit the timeout (rule 1) — a hung `browser-use` call will block the
  worker thread forever.
- Don't write a new status vocabulary per script — reuse the `s:` codes above
  so log lines are greppable across the project.

## Branch Workflow

`main` is always safe to run and always reflects what the user has manually
verified. Nothing lands on `main` without the user saying "merge it."

### Branch naming
- New feature → `feature/<short-slug>` (e.g. `feature/retry-backoff`)
- Bug fix (often found *after* a merge) → `fix/<short-slug>`
  (e.g. `fix/ws-reconnect`)
- Slug is lowercase-kebab, short, describes the unit of work.

### Creating a branch
```bash
git checkout main && git pull      # always start from fresh main
git checkout -b feature/<slug>     # or fix/<slug>
```
Never branch a fix off an old, already-merged feature branch — it may have
been deleted and its state is stale. Always off `main`.

### During the work
- Commit in small, meaningful chunks. Commit message style is the repo's
  existing convention: `<type>: <subject>` where type is `feat`, `fix`,
  `chore`, `test`, `refactor` (see `git log --oneline` for examples:
  `feat(backend): ...`, `fix: ...`, `test: ...`).
- Push the branch to origin if the user wants to track it: `git push -u
  origin feature/<slug>`.
- Don't rebase or force-push without asking.

### The merge gate — script tests are necessary but not sufficient
The user verifies in the *running app*, not just from test output. This is the
real gate, and it may span multiple sessions:

1. **Agent runs scripted tests** — `source venv/bin/activate && pytest`.
   Must pass before handoff. This is the floor, not the ceiling.
2. **Agent hands off for manual verification.** State explicitly: "ready for
   your verification" — never "done" or "complete." Tell the user what to
   check in the running app (specific UI flows, edge cases, the scenario the
   feature was built for).
3. **Expect back-and-forth.** The user finds a bug → agent diagnoses, fixes,
   re-runs tests, hands off again. This loop may repeat. A bug may warrant
   its own `fix/` branch off `main` even for in-flight feature work — ask the
   user if unclear.
4. **A single fix may need its own session.** Don't assume one session carries
   the whole verification cycle. A fresh-session agent picks up by reading
   this file + recent `git log` + the matching spec/plan (see Standard
   Workflow step 2).
5. **Merge only on explicit user approval.** The trigger is the user saying
   "merge it" (or equivalent). Agent never self-approves, never auto-merges,
   never opens a PR unless asked.

### Merging (only after the user says "merge it")
```bash
git checkout main && git pull
git merge --no-ff feature/<slug>    # preserves the branch's history
git push origin main                # if tracking remote
git branch -d feature/<slug>        # delete the local branch
git push origin --delete feature/<slug>   # if it was pushed
```
Same steps for `fix/<slug>` — identical gate, identical `--no-ff` merge,
identical cleanup.

### When NOT to merge
- Tests fail → fix first.
- User is unsure or hasn't verified in the app → wait.
- You (the agent) think it's done but the user hasn't said so → wait.

## Feature Process (non-trivial work)

Anything beyond a one-line fix goes through this four-stage flow. The repo
already uses it (see `docs/superpowers/specs/2026-07-05-ui-overhaul-design.md`
and the matching plan) — keep it consistent.

### What counts as "non-trivial"?
- Adds, removes, or meaningfully changes a feature
- Touches more than one file for a single purpose
- Changes a public interface (API endpoint, WS event shape, file output layout)
- Anything where a wrong assumption would waste real work

A typo fix, a log-message tweak, a single-line config bump → skip this flow,
just commit on a `feature/` or `fix/` branch.

### The four stages

1. **Brainstorm** — use the `brainstorming` skill. One question at a time,
   explore approaches, present a design in sections, get the user's approval
   per section. The user's explicit "yes" on the design ends this stage.
2. **Spec** — write the validated design to
   `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md` and commit it. Use the
   `elements-of-style:writing-clearly-and-concisely` skill if available. Do a
   quick self-review (no TBDs, no contradictions, no ambiguity, scope is
   single-plan-sized). Ask the user to review the file before proceeding.
3. **Plan** — invoke the `writing-plans` skill to produce the implementation
   plan in `docs/superpowers/plans/YYYY-MM-DD-<topic>.md`. Commit it.
4. **Implement** — on the `feature/<slug>` branch, work the plan, run tests,
   hand off for manual verification per Branch Workflow.

### Where to read state mid-flow
A fresh-session agent resuming in-flight work reads, in order:
1. This file (AGENTS.md).
2. `git log --oneline -15` on the current branch.
3. The newest matching spec in `docs/superpowers/specs/`.
4. The newest matching plan in `docs/superpowers/plans/`.
The spec + plan + commit history together are the source of truth for "what
are we doing and where are we." This file stays static on purpose.

## Picking Up In-Flight Work

A new session often starts mid-flight: a feature is half-built, a bug fix is
partially diagnosed, the user reported an intermittent issue last session and
we're here to chase it. The agent has no memory — reconstruct the state from
these sources, in this order:

### 1. Read this file first
You just did. The Standard Workflow and Branch Workflow govern everything
below.

### 2. Find the current branch and recent history
```bash
git branch                          # which branch am I on?
git log --oneline -15               # what's been done recently?
git log --oneline main..HEAD        # what's on this branch since main?
```
If `HEAD` is `main` and there's no in-flight work, the user will tell you
what's next — start the Feature Process fresh.

If you're on `feature/<slug>` or `fix/<slug>`, the commits since `main` tell
you how far implementation has gotten.

### 3. Read the matching spec and plan
```bash
ls -t docs/superpowers/specs/        # newest first
ls -t docs/superpowers/plans/
```
Open the spec + plan whose date and topic match the current branch's purpose.
The plan's remaining unchecked items are the next work. If the plan is fully
checked but the user reports a bug, you're in verification back-and-forth —
read the recent commits for what was just tried.

### 4. Ask the user what's wrong (don't assume)
Even with full context, ask: "Where are we — is this still implementing the
plan, or are you reporting a bug from verification?" Don't guess the state
from the files alone; the user knows whether the last build worked in the
running app.

### 5. Resume the correct loop
- **Still implementing** → continue the plan on the current branch.
- **Verification found a bug** → decide with the user: fix on the current
  `feature/` branch, or branch a `fix/` off `main`? Per Branch Workflow, a
  post-merge bug gets its own `fix/<slug>` off fresh `main`.
- **Bug is intermittent / runtime / UI-only** → scripted tests won't catch
  it. Use the Browser-Use Manual Playbook to reproduce in the running app,
  then write a fix. Don't claim "tests pass so it's done" — the user
  verifies in the app.

### 6. Before any new code
- Confirm you're on the right branch (not `main`).
- `git pull` if tracking remote.
- If starting a fix off `main`: `git checkout main && git pull && git
  checkout -b fix/<slug>`.

### Don't
- Don't assume the last session's mental model — read the spec/plan.
- Don't trust "tests pass" as the end state for an intermittent bug —
  reproduce it in the app first.
- Don't start coding before confirming the state with the user if anything
  is ambiguous.
