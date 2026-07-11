"""
Udemy Transcript Scraper Engine
Uses Selenium to extract transcripts from Udemy courses
via the browser's authenticated session. Sequential batch processing with
a single persistent driver.
"""
import json
import os
import re
import time
import logging

from driver import shared_manager
from selenium.common.exceptions import (
    TimeoutException,
    JavascriptException,
    WebDriverException,
    InvalidSessionIdException,
)

logger = logging.getLogger(__name__)


def extract_course_slug(url: str) -> str:
    """Extract course slug from a Udemy URL."""
    logger.info("[Scraper] Extracting course slug from URL: %s", url)
    patterns = [
        r"udemy\.com/course/([^/?#]+)",
        r"udemy\.com/course/([^/?#]+)/learn",
        r"udemy\.com/course/([^/?#]+)/overview",
    ]
    for i, pattern in enumerate(patterns):
        match = re.search(pattern, url)
        if match:
            slug = match.group(1)
            logger.info("[Scraper] Extracted slug: %s (matched pattern #%d)", slug, i + 1)
            return slug
    logger.error("[Scraper] Could not extract course slug from URL: %s", url)
    raise ValueError(f"Could not extract course slug from: {url}")


def sanitize_filename(name: str) -> str:
    """Sanitize a string for use as a filename."""
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = name.strip(". ")
    return name[:200] if name else "untitled"


class UdemyScraper:
    """Scrapes transcripts from a Udemy course using Selenium."""

    def __init__(self, log_callback=None):
        self.log = log_callback or (lambda msg: print(msg))
        self.course_id = None
        self.course_title = None
        self.course_slug = None
        self.sections = []
        self.output_dir = None
        self.driver = None
        logger.info("[Scraper] UdemyScraper initialized")

    def connect_and_navigate(self, url: str) -> bool:
        """Connect to the persistent driver and navigate to the course page."""
        logger.info("[Scraper] connect_and_navigate(%s)", url)
        t0 = time.time()

        self.course_slug = extract_course_slug(url)
        self.log(f"Course slug: {self.course_slug}")

        logger.info("[Scraper] Calling shared_manager.connect()...")
        self.driver = shared_manager.connect()
        logger.info("[Scraper] Driver connected (session=%s, URL=%s)", self.driver.session_id, self.driver.current_url)

        course_url = f"https://www.udemy.com/course/{self.course_slug}/learn"
        logger.info("[Scraper] Navigating to: %s", course_url)
        self.driver.get(course_url)
        logger.info("[Scraper] Navigation complete, current URL: %s", self.driver.current_url)

        logger.info("[Scraper] Waiting 2s for page to settle...")
        time.sleep(2)

        logger.info("[Scraper] Checking login status...")
        shared_manager.ensure_logged_in()

        elapsed = time.time() - t0
        logger.info("[Scraper] connect_and_navigate completed in %.2fs — URL: %s", elapsed, self.driver.current_url)
        self.log("Connected and navigated to course page.")
        return True

    def _js(self, js_body: str, timeout: int = 30) -> str:
        """Execute an async JS body in the browser and return the result string."""
        return shared_manager.execute_async_js(js_body, timeout=timeout)

    def _js_json(self, js_body: str, timeout: int = 30):
        """Execute an async JS body and parse the result as JSON.

        The callback shim converts JS rejections into a {"error": <msg>} JSON
        object, so check for that envelope here and raise a clear error rather
        than letting callers hit a KeyError on missing fields.
        """
        raw = self._js(js_body, timeout)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error("[Scraper] JSON decode failed: %s. Raw: %s", e, raw[:500] if raw else "(None)")
            raise
        if isinstance(parsed, dict) and "error" in parsed:
            raise RuntimeError(f"JS error: {parsed['error']}")
        return parsed

    def discover_course(self) -> dict:
        """Discover course info: ID, title, and full curriculum."""
        logger.info("[Scraper] discover_course() START")
        t0 = time.time()
        self.log("Discovering course info...")

        # Step 1: Fetch course info
        logger.info("[Scraper] Fetching course info for slug: %s", self.course_slug)
        code = f"""
    const resp = await fetch('/api-2.0/courses/{self.course_slug}/?fields[course]=id,title');
    const data = await resp.json();
    cb(JSON.stringify({{id: data.id, title: data.title}}));
"""
        logger.info("[Scraper] Executing JS: fetch course info...")
        course_info = self._js_json(code)
        logger.info("[Scraper] Course info response: %s", course_info)

        self.course_id = course_info["id"]
        self.course_title = course_info["title"]
        logger.info("[Scraper] Course ID: %d, Title: %s", self.course_id, self.course_title)
        self.log(f"Course: {self.course_title} (ID: {self.course_id})")

        # Step 2: Fetch curriculum
        logger.info("[Scraper] Fetching curriculum via GraphQL...")
        code = f"""
    const resp = await fetch('/api/2024-01/graphql/', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{
            query: `{{ course(id: {self.course_id}) {{ curriculum {{ sections {{ id title items {{ ... on Lecture {{ id title }} ... on Quiz {{ id title }} }} }} }} }} }}`
        }})
    }});
    const data = await resp.json();
    cb(JSON.stringify(data.data.course.curriculum.sections));
"""
        logger.info("[Scraper] Executing JS: fetch curriculum...")
        raw_sections = self._js_json(code)
        logger.info("[Scraper] Curriculum response: %d raw sections", len(raw_sections) if isinstance(raw_sections, list) else 0)
        if isinstance(raw_sections, list):
            for i, sec in enumerate(raw_sections):
                items = sec.get("items", [])
                logger.debug("[Scraper]   Section %d: id=%s title=%r items=%d", i, sec.get("id"), sec.get("title"), len(items))

        # Step 3: Parse sections
        self.sections = []
        for si, section in enumerate(raw_sections):
            lectures = []
            for item in section.get("items", []):
                if item.get("id") and item.get("title"):
                    lectures.append({
                        "id": item["id"],
                        "title": item["title"],
                    })
            self.sections.append({
                "index": si + 1,
                "id": section["id"],
                "title": section["title"],
                "lectures": lectures,
            })

        total_lectures = sum(len(s["lectures"]) for s in self.sections)
        elapsed = time.time() - t0
        logger.info("[Scraper] discover_course() completed in %.2fs — %d sections, %d lectures", elapsed, len(self.sections), total_lectures)
        self.log(f"Found {len(self.sections)} sections, {total_lectures} lectures")
        return {
            "course_id": self.course_id,
            "course_title": self.course_title,
            "sections": self.sections,
        }

    def create_folder_structure(self, base_dir: str) -> str:
        """Create the folder structure for the course."""
        logger.info("[Scraper] create_folder_structure(%s)", base_dir)
        t0 = time.time()

        folder_name = sanitize_filename(self.course_title)
        self.output_dir = os.path.join(base_dir, folder_name)
        logger.info("[Scraper] Creating output dir: %s", self.output_dir)
        os.makedirs(self.output_dir, exist_ok=True)

        for si, section in enumerate(self.sections):
            section_folder = f"{si+1:02d}_{sanitize_filename(section['title'].replace(' ', '_'))}"
            section["folder_name"] = section_folder
            section_path = os.path.join(self.output_dir, section_folder)
            os.makedirs(section_path, exist_ok=True)

        elapsed = time.time() - t0
        logger.info("[Scraper] Folder structure created in %.2fs at: %s", elapsed, self.output_dir)
        self.log(f"Created folder structure at: {self.output_dir}")
        return self.output_dir

    def fetch_transcripts_batch(self, lecture_ids: list, retries=2) -> dict:
        """
        Fetch transcripts for multiple lectures in parallel within a single
        async JS call. Returns dict: {lecture_id: {s: status, t: transcript}}.
        On driver-level failures, split the batch and retry recursively.
        """
        logger.info("[Scraper] fetch_transcripts_batch(%d lectures, retries=%d)", len(lecture_ids), retries)
        t0 = time.time()

        if not lecture_ids:
            logger.info("[Scraper] Empty lecture_ids, returning empty dict")
            return {}

        ids_json = json.dumps(lecture_ids)

        code = f"""
    const ids = {ids_json};
    const results = {{}};

    async function fetchOne(id) {{
        try {{
            const resp = await fetch('/api-2.0/users/me/subscribed-courses/{self.course_id}/lectures/' + id + '/?fields[lecture]=asset&fields[asset]=captions');
            if (!resp.ok) {{ results[id] = {{s: 'api_error', status: resp.status}}; return; }}

            const data = await resp.json();
            if (!data.asset || !data.asset.captions || data.asset.captions.length === 0) {{
                results[id] = {{s: 'no_captions'}}; return;
            }}

            let cap = data.asset.captions.find(c => c.locale_id.startsWith('en_') && c.source === 'manual');
            if (!cap) cap = data.asset.captions.find(c => c.locale_id.startsWith('en_'));
            if (!cap) {{ results[id] = {{s: 'no_english'}}; return; }}

            const vttResp = await fetch(cap.url);
            if (!vttResp.ok) {{ results[id] = {{s: 'vtt_error', status: vttResp.status}}; return; }}

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
    cb(JSON.stringify(results));
"""
        logger.info("[Scraper] Executing JS batch fetch for %d lectures...", len(lecture_ids))
        try:
            raw = self._js(code, timeout=60)
            results = json.loads(raw)

            # Log per-lecture results
            ok_count = sum(1 for v in results.values() if v.get("s") == "ok")
            error_count = sum(1 for v in results.values() if v.get("s") in ("error", "api_error"))
            skip_count = sum(1 for v in results.values() if v.get("s") in ("no_captions", "no_english", "empty"))
            logger.info("[Scraper] Batch results: %d ok, %d errors, %d skipped (of %d total)",
                        ok_count, error_count, skip_count, len(lecture_ids))

            elapsed = time.time() - t0
            logger.info("[Scraper] fetch_transcripts_batch completed in %.2fs", elapsed)
            return results

        except (TimeoutException, JavascriptException, WebDriverException, InvalidSessionIdException) as e:
            elapsed = time.time() - t0
            logger.error("[Scraper] Batch driver error after %.2fs: %s: %s", elapsed, type(e).__name__, str(e)[:200])
            if retries > 0 and len(lecture_ids) > 1:
                logger.info("[Scraper] Splitting batch (%d lectures) and retrying (retries=%d)...", len(lecture_ids), retries)
                time.sleep(0.5)
                mid = len(lecture_ids) // 2
                left = self.fetch_transcripts_batch(lecture_ids[:mid], retries - 1)
                right = self.fetch_transcripts_batch(lecture_ids[mid:], retries - 1)
                left.update(right)
                return left
            logger.error("[Scraper] No retries left — marking all %d lectures as failed", len(lecture_ids))
            return {lid: {"s": "error"} for lid in lecture_ids}

    def save_transcript(self, section: dict, lecture: dict, lecture_index: int, transcript: str):
        """Save a transcript to a file."""
        folder = os.path.join(self.output_dir, section["folder_name"])
        filename = f"{lecture_index:02d}_{sanitize_filename(lecture['title'].replace(' ', '_'))}.txt"
        filepath = os.path.join(folder, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"Lecture: {lecture['title']}\n")
            f.write(f"Section: {section['title']}\n")
            f.write(f"{'='*60}\n\n")
            f.write(transcript)

        return filepath

    def scrape_parallel(self, base_dir: str, progress_callback=None, stop_check=None,
                        batch_size=40, num_threads=3, skip_discovery=False):
        """
        Sequential batch scraping with a single persistent driver.
        (Name kept for backward compat; num_threads is ignored.)
        """
        logger.info("[Scraper] scrape_parallel() START")
        logger.info("[Scraper]   base_dir=%s, batch_size=%d, num_threads=%d, skip_discovery=%s",
                     base_dir, batch_size, num_threads, skip_discovery)
        t0 = time.time()

        if not skip_discovery:
            logger.info("[Scraper] Running course discovery...")
            self.discover_course()
            self.create_folder_structure(base_dir)
        else:
            logger.info("[Scraper] Using pre-discovered course: %s", self.course_title)

        all_lectures = []
        for si, section in enumerate(self.sections):
            for li, lecture in enumerate(section["lectures"]):
                all_lectures.append((si, li, lecture))

        logger.info("[Scraper] Total lectures: %d, batch_size=%d, batches=%d",
                     len(all_lectures), batch_size, (len(all_lectures) + batch_size - 1) // batch_size)

        self.log(f"Processing {len(all_lectures)} lectures in batches of {batch_size}")

        completed = 0
        failed = 0
        batch_num = 0

        for start in range(0, len(all_lectures), batch_size):
            batch_num += 1
            if stop_check and stop_check():
                logger.info("[Scraper] Stop requested at batch %d — halting", batch_num)
                self.log("Stop requested. Halting.")
                break

            batch = all_lectures[start:start + batch_size]
            batch_ids = [item[2]["id"] for item in batch]
            logger.info("[Scraper] ===== Batch %d: lectures %d-%d of %d (IDs: %s) =====",
                        batch_num, start + 1, min(start + batch_size, len(all_lectures)),
                        len(all_lectures), batch_ids)

            if progress_callback:
                for si, li, lec in batch:
                    progress_callback({
                        "type": "lecture_status",
                        "sectionIdx": si, "lectureIdx": li,
                        "status": "working",
                        "message": f"{lec['title'][:40]}",
                    })

            try:
                logger.info("[Scraper] Fetching transcripts for batch %d...", batch_num)
                results = self.fetch_transcripts_batch(batch_ids)
            except Exception as e:
                logger.error("[Scraper] Batch %d error: %s: %s", batch_num, type(e).__name__, e)
                self.log(f"Batch error: {e}")
                results = {lid: {"s": "error"} for lid in batch_ids}

            for si, li, lec in batch:
                result = results.get(lec["id"], {"s": "error"})
                status = result.get("s", "error")
                transcript = result.get("t", "")

                if status == "ok" and transcript:
                    self.save_transcript(self.sections[si], lec, li + 1, transcript)
                    completed += 1
                    logger.info("[Scraper]   OK: [%d.%d] %s (%d chars)", si + 1, li + 1, lec["title"][:50], len(transcript))
                    self.log(f"  Saved: {lec['title'][:50]} ({len(transcript)} chars)")
                    if progress_callback:
                        progress_callback({
                            "type": "lecture_status", "sectionIdx": si, "lectureIdx": li,
                            "status": "saved", "message": f"Saved: {lec['title'][:50]}",
                            "size": len(transcript),
                        })
                elif status in ("no_captions", "no_english", "api_error"):
                    completed += 1
                    logger.info("[Scraper]   SKIP: [%d.%d] %s (%s)", si + 1, li + 1, lec["title"][:50], status)
                    if progress_callback:
                        progress_callback({
                            "type": "lecture_status", "sectionIdx": si, "lectureIdx": li,
                            "status": "skipped", "message": f"Skipped ({status})",
                        })
                else:
                    failed += 1
                    logger.warning("[Scraper]   FAIL: [%d.%d] %s (status=%s, error=%s)",
                                   si + 1, li + 1, lec["title"][:50], status, result.get("m", ""))
                    self.log(f"  Failed ({status}): {lec['title'][:50]}")
                    if progress_callback:
                        progress_callback({
                            "type": "lecture_status", "sectionIdx": si, "lectureIdx": li,
                            "status": "failed", "message": f"Failed ({status})",
                        })

            logger.info("[Scraper] Batch %d done: completed=%d, failed=%d", batch_num, completed, failed)
            time.sleep(0.3)

        elapsed = time.time() - t0
        logger.info("[Scraper] scrape_parallel() END: %d completed, %d failed in %.2fs", completed, failed, elapsed)
        self.log(f"\nDone! {completed} completed, {failed} failed.")
        if progress_callback:
            progress_callback({"type": "scrape_finished", "completed": completed, "failed": failed})
