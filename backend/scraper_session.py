import os
import sys
import queue as tqueue
import threading
import time
import traceback
import logging

from scraper import UdemyScraper
from progress_tracker import ProgressTracker
from driver import get_last_driver_error

logger = logging.getLogger(__name__)


NATIVE_TO_BOX = {
    "working": "in-progress",
    "saved": "success",
    "skipped": "skipped",
    "failed": "failed",
}


def normalize_status(native: str) -> str:
    normalized = NATIVE_TO_BOX.get(native, "failed")
    return normalized


class ScraperSession:
    def __init__(self):
        logger.info("[Session] Creating new ScraperSession instance (PID=%d)", os.getpid())
        self._tqueue = tqueue.Queue()
        self.scraper = None
        self.tracker = None
        self.thread = None
        self.stop_flag = False
        self.is_running = False
        self.started_at = None
        self.course_snapshot = None
        self.lecture_states = {}
        logger.info("[Session] ScraperSession initialized")

    def _emit(self, event: dict):
        self._tqueue.put_nowait(event)

    def _on_scraper_event(self, event: dict):
        event_type = event.get("type")
        if event_type == "lecture_status":
            key = (event["sectionIdx"], event["lectureIdx"])
            normalized = normalize_status(event["status"])
            self.lecture_states[key] = normalized
            # Update tracker when lecture succeeds
            if normalized == "success" and self.tracker:
                scraper = self.scraper
                if scraper and scraper.sections:
                    try:
                        sec = scraper.sections[event["sectionIdx"]]
                        lec = sec["lectures"][event["lectureIdx"]]
                        self.tracker.mark_lecture_done(lec["id"], event["sectionIdx"], event["lectureIdx"])
                    except (IndexError, KeyError):
                        pass
            self._emit({
                "type": "lecture_status",
                "sectionIdx": event["sectionIdx"],
                "lectureIdx": event["lectureIdx"],
                "status": normalized,
                "message": event.get("message", ""),
                "size": event.get("size"),
            })
            self._emit_progress()
        elif event_type == "scrape_finished":
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
        states = list(self.lecture_states.values())
        success = states.count("success")
        active = states.count("in-progress")
        skipped = states.count("skipped")
        failed = states.count("failed")
        total = self.tracker.state.get("total_lectures", 0) if self.tracker else 0
        elapsed_ms = int((time.time() - self.started_at) * 1000) if self.started_at else 0
        return {
            "completed": success, "total": total, "failed": failed,
            "skipped": skipped, "active": active, "success": success,
            "elapsedMs": elapsed_ms,
        }

    def _emit_progress(self):
        self._emit({"type": "progress", **self._counts()})

    def _run(self, url: str, output_dir: str, batch_size: int, num_threads: int, resume: bool):
        logger.info("[Session] _run() START")
        logger.info("[Session]   url=%s", url)
        logger.info("[Session]   output_dir=%s", output_dir)
        logger.info("[Session]   batch_size=%d", batch_size)
        logger.info("[Session]   num_threads=%d", num_threads)
        logger.info("[Session]   resume=%s", resume)
        logger.info("[Session]   PID=%d, Thread=%s", os.getpid(), threading.current_thread().name)

        output_dir = os.path.expanduser(output_dir)
        logger.info("[Session] Expanded output_dir: %s", output_dir)

        try:
            logger.info("[Session] Creating UdemyScraper...")
            self.scraper = UdemyScraper(log_callback=self._on_log)

            logger.info("[Session] Creating ProgressTracker for %s...", output_dir)
            self.tracker = ProgressTracker(output_dir)

            if resume:
                slug = self.tracker.state.get("course_slug")
                if slug:
                    url = f"https://www.udemy.com/course/{slug}/learn"
                    logger.info("[Session] Resuming course slug=%s, URL=%s", slug, url)
                    self._emit({"type": "log", "message": f"Resumed course: {slug}", "level": "info"})
                else:
                    logger.warning("[Session] Resume requested but no course_slug in tracker state")

            logger.info("[Session] Step 1: Connecting to browser...")
            self._emit({"type": "log", "message": "Connecting to browser...", "level": "info"})
            try:
                self.scraper.connect_and_navigate(url)
            except Exception as conn_err:
                logger.error("[Session] Browser connection failed: %s: %s", type(conn_err).__name__, conn_err)
                print(f"\n[DRIVER ERROR] {type(conn_err).__name__}: {conn_err}", file=sys.stderr)
                tb = traceback.format_exc()
                print(tb, file=sys.stderr)
                sys.stderr.flush()
                self._emit({"type": "error", "message": str(conn_err)})
                return

            logger.info("[Session] Step 2: Discovering course structure...")
            self._emit({"type": "log", "message": "Discovering course structure...", "level": "info"})
            self.scraper.discover_course()
            logger.info("[Session] Course discovered: %s (ID=%s, %d sections)",
                        self.scraper.course_title, self.scraper.course_id, len(self.scraper.sections))

            logger.info("[Session] Step 3: Initializing progress tracker...")
            total_lectures = sum(len(s["lectures"]) for s in self.scraper.sections)
            self.tracker.init_course(
                self.scraper.course_slug, self.scraper.course_id, self.scraper.course_title,
                len(self.scraper.sections),
                total_lectures,
                output_dir,
            )
            logger.info("[Session] Tracker initialized: %d sections, %d lectures", len(self.scraper.sections), total_lectures)

            logger.info("[Session] Step 4: Creating folder structure...")
            self.scraper.create_folder_structure(output_dir)

            logger.info("[Session] Step 5: Building course snapshot for UI...")
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
                logger.info("[Session] Step 6: Marking previously completed lectures...")
                resumed_count = 0
                for si, section in enumerate(self.scraper.sections):
                    for li, lec in enumerate(section["lectures"]):
                        if self.tracker.is_lecture_done(lec["id"]):
                            self.lecture_states[(si, li)] = "success"
                            self._emit({"type": "lecture_status", "sectionIdx": si,
                                        "lectureIdx": li, "status": "success",
                                        "message": "Resumed", "size": None})
                            resumed_count += 1
                logger.info("[Session] Resumed %d previously completed lectures", resumed_count)

            self._emit_progress()

            if num_threads and num_threads != 1:
                logger.info("[Session] Note: num_threads=%d ignored (single-driver sequential mode)", num_threads)
                self._emit({"type": "log",
                            "message": f"num_threads={num_threads} ignored (single-driver sequential mode)",
                            "level": "info"})

            logger.info("[Session] Step 7: Starting scrape_parallel...")
            self.scraper.scrape_parallel(
                base_dir=output_dir,
                progress_callback=self._on_scraper_event,
                stop_check=lambda: self.stop_flag,
                batch_size=batch_size,
                num_threads=num_threads,
                skip_discovery=True,
            )
            logger.info("[Session] scrape_parallel returned")
            self._emit_progress()

        except Exception as e:
            logger.error("[Session] FATAL error in _run: %s: %s", type(e).__name__, e)
            print(f"\n[FATAL] {type(e).__name__}: {e}", file=sys.stderr)
            print(traceback.format_exc(), file=sys.stderr)
            sys.stderr.flush()
            self._emit({"type": "error", "message": str(e)})
        finally:
            logger.info("[Session] _run() FINALLY block — setting is_running=False")
            self.is_running = False
            self.stop_flag = False

    def start(self, url: str, output_dir: str, batch_size: int, num_threads: int, resume: bool = False):
        logger.info("[Session] start() called: url=%s, output_dir=%s, batch_size=%d, num_threads=%d, resume=%s",
                     url, output_dir, batch_size, num_threads, resume)
        self.stop_flag = False
        self.is_running = True
        self.started_at = time.time()
        logger.info("[Session] Creating daemon thread for _run()...")
        self.thread = threading.Thread(
            target=self._run,
            args=(url, output_dir, batch_size, num_threads, resume),
            daemon=True,
            name="ScraperThread",
        )
        self.thread.start()
        logger.info("[Session] Thread started (tid=%s)", self.thread.ident)

    def stop(self):
        logger.info("[Session] stop() called — setting stop_flag=True")
        self.stop_flag = True

    def retry_failed(self):
        logger.info("[Session] retry_failed() called")
        to_retry = []
        for (si, li), st in list(self.lecture_states.items()):
            if st == "failed":
                lec = self.scraper.sections[si]["lectures"][li]
                to_retry.append((si, li, lec["id"], lec["title"]))
                self.lecture_states[(si, li)] = "in-progress"
                self._emit({"type": "lecture_status", "sectionIdx": si, "lectureIdx": li,
                            "status": "in-progress", "message": "Retrying", "size": None})

        if not to_retry:
            logger.info("[Session] No failed lectures to retry")
            return

        logger.info("[Session] Retrying %d failed lectures: %s", len(to_retry), [(t[3][:30]) for t in to_retry])
        self._emit_progress()
        self.is_running = True
        thread = threading.Thread(target=self._run_retry, args=(to_retry,), daemon=True, name="RetryThread")
        thread.start()
        logger.info("[Session] Retry thread started")

    def _run_retry(self, to_retry: list):
        logger.info("[Session] _run_retry() START for %d lectures", len(to_retry))
        t0 = time.time()
        try:
            scraper = self.scraper
            batch_ids = [item[2] for item in to_retry]
            logger.info("[Session] Fetching transcripts for retry batch: %s", batch_ids)
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
                    logger.info("[Session] Retry OK: [%d.%d] %s (%d chars)", si + 1, li + 1, title[:40], len(transcript))
                    self._emit({"type": "lecture_status", "sectionIdx": si, "lectureIdx": li,
                                "status": "success", "message": f"Retry OK: {title[:40]}",
                                "size": len(transcript)})
                elif status in ("no_captions", "no_english", "api_error"):
                    self.lecture_states[(si, li)] = "skipped"
                    logger.info("[Session] Retry SKIP: [%d.%d] %s (%s)", si + 1, li + 1, title[:40], status)
                    self._emit({"type": "lecture_status", "sectionIdx": si, "lectureIdx": li,
                                "status": "skipped", "message": f"Retry skipped ({status})"})
                else:
                    self.lecture_states[(si, li)] = "failed"
                    logger.warning("[Session] Retry FAIL: [%d.%d] %s (%s)", si + 1, li + 1, title[:40], status)
                    self._emit({"type": "lecture_status", "sectionIdx": si, "lectureIdx": li,
                                "status": "failed", "message": f"Retry failed ({status})"})

            self._emit_progress()
        except Exception as e:
            logger.error("[Session] _run_retry() FAILED: %s: %s", type(e).__name__, e)
            self._emit({"type": "error", "message": f"Retry error: {e}"})
        finally:
            elapsed = time.time() - t0
            logger.info("[Session] _run_retry() FINALLY — completed in %.2fs", elapsed)
            self.is_running = False

    async def events(self):
        import asyncio
        if self.course_snapshot is not None:
            yield {"type": "course_discovered", **self.course_snapshot}
            for (si, li), st in self.lecture_states.items():
                yield {"type": "lecture_status", "sectionIdx": si, "lectureIdx": li,
                       "status": st, "message": "", "size": None}
            yield {"type": "progress", **self._counts()}
            if not self.is_running:
                yield {"type": "done", **self._counts()}
                return
        loop = asyncio.get_running_loop()
        while True:
            event = await loop.run_in_executor(None, self._tqueue.get)
            yield event
            if event.get("type") in ("done", "error"):
                logger.info("[Session] events(): received %s — breaking", event.get("type"))
                break
