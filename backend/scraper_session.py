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
        self.lecture_states = {}

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
