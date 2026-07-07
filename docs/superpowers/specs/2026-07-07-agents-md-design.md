# AGENTS.md Design Spec

Design for a comprehensive development-workflow `AGENTS.md` that gives any
fresh-session agent the context it needs to work on this project
consistently across sessions — project overview, browser-use guidance
(manual and scripted), feature-branch workflow with the user as the merge
gate, and a procedure for picking up in-flight work.

## Non-goals

- This spec is for `AGENTS.md` only. It does not design the app, the scraper,
  or any feature.
- `AGENTS.md` itself stays static — living state (what's in-flight, what was
  tried, what's failing) lives in `docs/superpowers/specs/`,
  `docs/superpowers/plans/`, and `git log`. AGENTS.md only routes agents to
  those sources.

## Structure (workflow-first, reference-second)

The file opens with an ordered Standard Workflow checklist — the contract
every session reads first — then backs each step with reference sections.

### Section 1 — Standard Workflow (opening checklist)

The opening section, right after the title. Nine ordered steps:

1. **Orient.** Read this file top-to-bottom. Then `git log --oneline -15`
   and `git branch` to see recent work and the current branch.
2. **Find in-flight context.** If work is mid-flight, the context lives in
   `docs/superpowers/specs/` and `docs/superpowers/plans/` (newest by date)
   plus the commit history of the current feature branch — NOT in this
   file. Open the most recent spec + plan that match the current branch's
   purpose.
3. **One unit of work = one branch, never commit to `main`.**
   - New feature → `feature/<short-slug>` off the latest `main`.
   - Bug fix (often found *after* a merge) → `fix/<short-slug>` off the
     latest `main` (`git checkout main && git pull` first). Never branch a
     fix off the old feature branch — it's already merged and may be
     deleted.
4. **Design before code (non-trivial work).** Anything beyond a one-line
   fix goes through brainstorm → spec → plan → implement. Spec lands in
   `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`; plan in
   `docs/superpowers/plans/`.
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
   delete the feature branch.
9. **Update context for the next session.** Commit the spec, the plan,
   and any debug notes/scratch files that would let a fresh agent pick up
   where you left off. This file stays static; the living state lives in
   specs, plans, and git.

### Section 2 — Project Overview

Short prose: a desktop app that scrapes transcripts from Udemy courses the
user is enrolled in, using the user's already-logged-in Chrome session —
no Udemy credentials handled by the app. The user pastes a course URL,
picks an output directory, and the app downloads English transcripts
organized by section/lecture into that directory.

Component breakdown:
- `scraper.py` — scraping engine. Shells out to `browser-use` (via
  subprocess) to run JavaScript inside the user's Chrome tab, hitting
  Udemy's own API/GraphQL endpoints from the authenticated session.
  Transcripts fetched in parallel batches with a work queue and worker
  threads.
- `backend/server.py` — FastAPI app exposing `/api/start`, `/api/stop`,
  `/api/resume`, `/api/retry-failed`, plus a `/ws` WebSocket streaming
  per-lecture progress events to the UI.
- `backend/scraper_session.py` — adapter wrapping `UdemyScraper` into the
  event stream the server expects (normalizes statuses, owns the thread).
- `app.py` — launcher. Starts FastAPI on 127.0.0.1:8765, opens a PyWebView
  window at that URL; falls back to system browser if
  PyWebView/Qt/GTK unavailable.
- `frontend/` — vanilla JS/HTML/CSS, no build step. `app.js` holds
  `ScraperUI` (state + render + WS client + commands).
- `progress_tracker.py` — persists resume state to `scrape_state.json`
  in the output dir.

Key constraint: everything rides on the user's logged-in Udemy session in
Chrome. If Chrome isn't running, isn't logged in, or remote debugging
isn't allowed, `browser-use` can't connect and nothing works.

### Section 3 — Setup & Commands

First-time setup (venv, pip install -r requirements.txt; note browser-use
installed separately and must be on PATH / able to attach to Chrome CDP).
Run the app (`./run.sh` or manual). Tests (`pytest` from repo root; httpx
for server tests; no live Udemy/Chrome calls in the suite). Prerequisites
before any scrape works: Chrome running, remote debugging allowed, user
logged into Udemy. Diagnostic: `browser-use --doctor`.

### Section 4 — Architecture Map

ASCII diagram of the data flow:
browser-use (subprocess) → UdemyScraper (connect/discover/fetch/save) →
ScraperSession (adapter, WS events) → FastAPI (REST + WS) → frontend/app.js
→ PyWebView. progress_tracker writes scrape_state.json.

Three gotchas:
- `browser-use` is a subprocess call, not an imported library. The CDP
  daemon owns the connection.
- JS runs in the page's own context, so it has the user's Udemy cookies.
  API shape changes break the JS, not the Python.
- Progress is pushed, not polled. No GET-status endpoint; UI only reacts
  to WS events. If UI looks stuck, check the WS connection in devtools.

### Section 5 — Browser-Use Manual Playbook

Four manual workflows for when search tools (Read/Grep/Glob) can't reach
the answer:

1. **Reverse-engineering site APIs / structure** — `new_tab` →
   `wait_for_load` → `js(fetch...)` from the page context so cookies
   apply. Always read `scraper.py` first; don't re-derive Udemy endpoints
   from scratch.
2. **Debugging scraper failures** — `capture_screenshot` →
   `ensure_real_tab` → `js(document.title)` to check for login bounce;
   replay the exact fetch scraper.py would do with raw status+body
   logging. Common causes: session expired, not enrolled, lecture is a
   quiz, captions only in non-English locales.
3. **Multi-step interactive exploration** — screenshot → read pixel →
   `click_at_xy` → screenshot → `js(...)`. Never guess a selector blind.
   Shadow root / cross-origin iframe → check interaction-skills docs in
   the browser-use SKILL.md.
4. **General scraping for new features** — same playbook against a new
   host.

Stop and ask the user on: any login/password/MFA/consent prompt (SSO
auto-continue only if Chrome already signed in); `browser-use --doctor`
can't connect after the user enables remote debugging; page says access
denied (fix is on the user's account, not in code).

### Section 6 — Browser-Use Scripted Patterns

Codifies the patterns already in `scraper.py`. Canonical shape is
`run_browser_use()` wrapping a heredoc in `subprocess.run` with a
timeout. Four rules every script must follow:

1. **Timeout every call.** 30s default for inspection, 60s for batched
   fetches.
2. **Retry by splitting, not re-running the whole batch.** On failure with
   a batch of N, split in half, sleep 0.5s, recurse to bounded depth.
   Verbatim from `scraper.py:fetch_transcripts_batch`.
3. **Cap parallelism; stagger worker starts.** `queue.Queue` work queue,
   3 worker threads default, start 0.5s apart to avoid a CDP stampede.
4. **Classify every result; never lose one.** Status dict with `s:` codes:
   `ok | no_captions | no_english | api_error | vtt_error | empty | error`.
   Caller switches on `s` to decide saved/skipped/failed. Reuse this
   vocabulary for any new script so log lines are greppable across the
   project.

Decision table for script vs. manual: one-off inspection → manual; "what
does the API look like?" → manual; fetch N lectures → script; scrape a
new page type at scale → script; debug one lecture → manual; anything
interactive → manual.

Don'ts: don't import browser-use as a library (always subprocess +
heredoc); don't retry by looping the same full batch (split); don't omit
the timeout; don't invent a new status vocabulary per script.

### Section 7 — Branch Workflow

`main` is always safe to run and reflects what the user has manually
verified. Nothing lands on `main` without the user saying "merge it."

- **Branch naming:** feature → `feature/<short-slug>`; bug fix →
  `fix/<short-slug>`. Slug lowercase-kebab.
- **Creating:** `git checkout main && git pull` then
  `git checkout -b feature/<slug>` (or `fix/<slug>`). Never branch a fix
  off an old, merged feature branch.
- **During work:** commit small chunks; message style is
  `<type>: <subject>` (`feat`, `fix`, `chore`, `test`, `refactor`), with
  optional scope like `feat(backend): ...` — see `git log --oneline` for
  examples. Push to origin if the user wants to track. Don't rebase or
  force-push without asking.

Merge gate — script tests are necessary but not sufficient:
1. Agent runs `pytest`; must pass before handoff. Floor, not ceiling.
2. Agent hands off for manual verification: "ready for your verification"
   (never "done" or "complete"), telling the user what to check in the
   running app.
3. Expect back-and-forth. User finds a bug → agent diagnoses, fixes,
   re-runs tests, hands off again. May repeat. A bug may warrant its own
   `fix/` branch off `main` even for in-flight feature work — ask the
   user if unclear.
4. A single fix may need its own session. A fresh-session agent picks up
   by reading this file + recent `git log` + the matching spec/plan.
5. Merge only on explicit user approval ("merge it" or equivalent).
   Agent never self-approves, never auto-merges, never opens a PR unless
   asked.

Merging (only after the user says "merge it"):
`git checkout main && git pull && git merge --no-ff feature/<slug>` then
`git push origin main` if tracking, then delete the feature branch
locally and on origin. Same steps for `fix/<slug>`.

When NOT to merge: tests fail; user is unsure or hasn't verified in the
app; agent thinks it's done but the user hasn't said so.

### Section 8 — Feature Process (non-trivial work)

The repo's existing four-stage flow (see
`docs/superpowers/specs/2026-07-05-ui-overhaul-design.md` and the matching
plan). Keep it consistent.

Non-trivial = adds/removes/meaningfully changes a feature; touches more
than one file for a single purpose; changes a public interface (API
endpoint, WS event shape, file output layout); or anything where a wrong
assumption would waste real work. A typo fix / log-message tweak /
single-line config bump skips this flow — just commit on a branch.

Four stages:
1. **Brainstorm** — `brainstorming` skill. One question at a time,
   approaches with trade-offs, design in sections, user approval per
   section.
2. **Spec** — write to
   `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`, commit. Use
   `elements-of-style:writing-clearly-and-concisely` if available.
   Self-review for TBDs / contradictions / ambiguity / scope. Ask the
   user to review before proceeding.
3. **Plan** — invoke `writing-plans` skill; plan in
   `docs/superpowers/plans/YYYY-MM-DD-<topic>.md`. Commit.
4. **Implement** — on `feature/<slug>`; work the plan; run tests; hand
   off per Branch Workflow.

Where to read state mid-flow: this file; `git log --oneline -15` on the
current branch; newest matching spec; newest matching plan. Together they
are the source of truth for "what are we doing and where are we."

### Section 9 — Picking Up In-Flight Work

The section a fresh-session agent lands on when resuming mid-cycle.

1. **Read this file first** (Standard Workflow + Branch Workflow govern
   everything).
2. **Find the current branch and recent history:** `git branch`,
   `git log --oneline -15`, `git log --oneline main..HEAD`. If on `main`
   with no in-flight work, the user says what's next — start the Feature
   Process fresh. If on `feature/` or `fix/`, commits since `main` show
   how far implementation has gotten.
3. **Read the matching spec and plan** (`ls -t
   docs/superpowers/specs/`, `ls -t docs/superpowers/plans/`). Open the
   ones whose date and topic match the current branch's purpose. Plan's
   unchecked items are the next work. Plan fully checked but user
   reports a bug → verification back-and-forth; read recent commits for
   what was just tried.
4. **Ask the user what's wrong** — don't assume. "Is this still
   implementing the plan, or are you reporting a bug from
   verification?"
5. **Resume the correct loop:**
   - Still implementing → continue the plan on the current branch.
   - Verification found a bug → decide with user: fix on the current
     `feature/` branch, or branch a `fix/` off `main`? Post-merge bug
     gets its own `fix/<slug>` off fresh `main`.
   - Bug is intermittent / runtime / UI-only → scripted tests won't
     catch it. Use the Browser-Use Manual Playbook to reproduce in the
     running app, then write a fix. Don't claim "tests pass so it's
     done" — the user verifies in the app.
6. **Before any new code:** confirm you're on the right branch (not
   `main`); `git pull` if tracking remote; if starting a fix off `main`:
   `git checkout main && git pull && git checkout -b fix/<slug>`.

Don'ts: don't assume the last session's mental model — read the
spec/plan; don't trust "tests pass" as the end state for an intermittent
bug — reproduce it in the app first; don't start coding before
confirming the state with the user if anything is ambiguous.

## File placement

The AGENTS.md file goes at the repository root: `/AGENTS.md`. This is the
conventional location that opencode and most agent tools read
automatically.

## Order in the final file

1. Title + Standard Workflow (the opening checklist)
2. Project Overview
3. Setup & Commands
4. Architecture Map
5. Browser-Use Manual Playbook
6. Browser-Use Scripted Patterns
7. Branch Workflow
8. Feature Process (non-trivial work)
9. Picking Up In-Flight Work

The Standard Workflow checklist references the later sections by name so
an agent knows where to look for depth on each step.
