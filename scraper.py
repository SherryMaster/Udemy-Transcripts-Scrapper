"""
Udemy Transcript Scraper Engine
Uses browser-use to extract transcripts from Udemy courses via the browser's authenticated session.
Multi-threaded with work queue for parallel processing.
"""
import json
import os
import queue
import re
import subprocess
import threading
import time


def run_browser_use(code: str, timeout: int = 30) -> str:
    """Run code in the browser via browser-use and return the result."""
    wrapped = f"browser-use <<'BROWSER_USE_EOF'\n{code}\nBROWSER_USE_EOF"
    result = subprocess.run(
        ["bash", "-c", wrapped],
        capture_output=True, text=True, timeout=timeout
    )
    if result.returncode != 0:
        raise RuntimeError(f"browser-use failed: {result.stderr[:500]}")
    return result.stdout.strip()


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
    """Scrapes transcripts from a Udemy course using browser-use."""

    def __init__(self, log_callback=None):
        self.log = log_callback or (lambda msg: print(msg))
        self.course_id = None
        self.course_title = None
        self.course_slug = None
        self.sections = []
        self.output_dir = None

    def _js(self, js_expression: str, timeout: int = 30) -> str:
        """Execute JavaScript in the browser context via js() helper."""
        python_code = f'result = js("""{js_expression}""")\nprint(result)'
        return run_browser_use(python_code, timeout)

    def _js_json(self, js_expression: str, timeout: int = 30):
        """Execute JavaScript and parse the result as JSON."""
        raw = self._js(js_expression, timeout)
        return json.loads(raw)

    def connect_and_navigate(self, url: str) -> bool:
        """Connect to browser and navigate to the course page."""
        self.course_slug = extract_course_slug(url)
        self.log(f"Course slug: {self.course_slug}")

        nav_code = f"""
new_tab("https://www.udemy.com/course/{self.course_slug}/learn")
wait_for_load()
print(page_info())
"""
        result = run_browser_use(nav_code, timeout=30)
        self.log(f"Navigation result: {result[:200]}")

        if "udemy.com" not in result:
            raise RuntimeError("Failed to navigate to course page. Is Chrome open and logged in?")
        return True

    def discover_course(self) -> dict:
        """Discover course info: ID, title, and full curriculum."""
        self.log("Discovering course info...")

        code = f"""
(async () => {{
    const resp = await fetch('/api-2.0/courses/{self.course_slug}/?fields[course]=id,title');
    const data = await resp.json();
    return JSON.stringify({{id: data.id, title: data.title}});
}})()
"""
        course_info = self._js_json(code)
        self.course_id = course_info["id"]
        self.course_title = course_info["title"]
        self.log(f"Course: {self.course_title} (ID: {self.course_id})")

        code = f"""
(async () => {{
    const resp = await fetch('/api/2024-01/graphql/', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{
            query: `{{ course(id: {self.course_id}) {{ curriculum {{ sections {{ id title items {{ ... on Lecture {{ id title }} ... on Quiz {{ id title }} }} }} }} }} }}`
        }})
    }});
    const data = await resp.json();
    return JSON.stringify(data.data.course.curriculum.sections);
}})()
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
        Fetch transcripts for multiple lectures in parallel within a single JS call.
        Returns dict: {lecture_id: {s: status, t: transcript}}
        """
        if not lecture_ids:
            return {}

        ids_json = json.dumps(lecture_ids)
        code = f"""
(async () => {{
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
}})()
"""
        try:
            raw = self._js(code, timeout=60)
            return json.loads(raw)
        except Exception as e:
            if retries > 0 and len(lecture_ids) > 1:
                mid = len(lecture_ids) // 2
                time.sleep(0.5)
                left = self.fetch_transcripts_batch(lecture_ids[:mid], retries - 1)
                right = self.fetch_transcripts_batch(lecture_ids[mid:], retries - 1)
                left.update(right)
                return left
            raise

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
                        batch_size=4, num_threads=3, skip_discovery=False):
        """
        Multi-threaded scraping with work queue.
        If skip_discovery=True, assumes discover_course() was already called.
        """
        if not skip_discovery:
            self.discover_course()
            self.create_folder_structure(base_dir)
        else:
            self.log(f"Using pre-discovered course: {self.course_title}")

        # Build work queue: all lectures (quizzes will fail fast with api_error)
        work_queue = queue.Queue()
        total_lectures = 0

        for si, section in enumerate(self.sections):
            for li, lecture in enumerate(section["lectures"]):
                total_lectures += 1
                work_queue.put((si, li, lecture))

        self.log(f"Work queue: {work_queue.qsize()} items to process")

        # Shared state
        completed = [0]
        failed = [0]
        lock = threading.Lock()
        stop_flag = [False]

        def worker(worker_id):
            """Worker thread: pull batches from queue and process."""
            scraper = UdemyScraper(log_callback=self.log)
            scraper.course_id = self.course_id
            scraper.output_dir = self.output_dir

            while not stop_flag[0]:
                # Check external stop
                if stop_check and stop_check():
                    stop_flag[0] = True
                    break

                # Gather a batch from the queue
                batch = []
                while len(batch) < batch_size and not work_queue.empty():
                    try:
                        item = work_queue.get_nowait()
                        batch.append(item)
                    except queue.Empty:
                        break

                if not batch:
                    break  # Queue empty, done

                batch_ids = [item[2]["id"] for item in batch]

                # Report working
                if progress_callback:
                    for si, li, lec in batch:
                        progress_callback({
                            "type": "lecture_status",
                            "sectionIdx": si, "lectureIdx": li,
                            "status": "working",
                            "message": f"[W{worker_id}] {lec['title'][:40]}",
                        })

                # Fetch batch
                try:
                    results = self.fetch_transcripts_batch(batch_ids)
                except Exception as e:
                    self.log(f"  [W{worker_id}] Batch error: {e}")
                    results = {lid: {"s": "error"} for lid in batch_ids}

                # Process results
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

                # Small delay between batches per worker
                time.sleep(0.3)

        # Launch workers with staggered starts
        threads = []
        for i in range(num_threads):
            t = threading.Thread(target=worker, args=(i + 1,), daemon=True)
            threads.append(t)
            t.start()
            time.sleep(0.5)  # Stagger starts to avoid CDP stampede

        # Wait for all workers
        for t in threads:
            t.join()

        self.log(f"\nDone! {completed[0]} completed, {failed[0]} failed.")
        if progress_callback:
            progress_callback({"type": "scrape_finished", "completed": completed[0], "failed": failed[0]})
