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


def extract_course_slug(url: str) -> str:
    """Extract course slug from a Udemy URL."""
    patterns = [
        r"udemy\.com/course/([^/?#]+)",
        r"udemy\.com/course/([^/?#]+)/learn",
        r"udemy\.com/course/([^/?#]+)/overview",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise ValueError(f"Could not extract course slug from: {url}")


def sanitize_filename(name: str) -> str:
    """Sanitize a string for use as a filename."""
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = name.strip(". ")
    return name[:200] if name else "untitled"


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

    def _js(self, js_body: str, timeout: int = 30) -> str:
        """Execute an async JS body in the browser and return the result string."""
        return shared_manager.execute_async_js(js_body, timeout=timeout)

    def _js_json(self, js_body: str, timeout: int = 30):
        """Execute an async JS body and parse the result as JSON."""
        raw = self._js(js_body, timeout)
        return json.loads(raw)

    def discover_course(self) -> dict:
        """Discover course info: ID, title, and full curriculum."""
        self.log("Discovering course info...")

        code = f"""
    const resp = await fetch('/api-2.0/courses/{self.course_slug}/?fields[course]=id,title');
    const data = await resp.json();
    cb(JSON.stringify({{id: data.id, title: data.title}}));
"""
        course_info = self._js_json(code)
        self.course_id = course_info["id"]
        self.course_title = course_info["title"]
        self.log(f"Course: {self.course_title} (ID: {self.course_id})")

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
        raw_sections = self._js_json(code)

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
        self.log(f"Found {len(self.sections)} sections, {total_lectures} lectures")
        return {
            "course_id": self.course_id,
            "course_title": self.course_title,
            "sections": self.sections,
        }

    def create_folder_structure(self, base_dir: str) -> str:
        """Create the folder structure for the course."""
        folder_name = sanitize_filename(self.course_title)
        self.output_dir = os.path.join(base_dir, folder_name)
        os.makedirs(self.output_dir, exist_ok=True)

        for si, section in enumerate(self.sections):
            section_folder = f"{si+1:02d}_{sanitize_filename(section['title'].replace(' ', '_'))}"
            section["folder_name"] = section_folder
            section_path = os.path.join(self.output_dir, section_folder)
            os.makedirs(section_path, exist_ok=True)

        self.log(f"Created folder structure at: {self.output_dir}")
        return self.output_dir

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
    cb(JSON.stringify(results));
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