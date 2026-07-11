"""
Progress Tracker for Udemy Transcript Scraper
Saves and loads scraping progress so it can resume after interruption.
"""
import json
import os
import time
import logging

logger = logging.getLogger(__name__)


class ProgressTracker:
    """Tracks and persists scraping progress for resume capability."""

    STATE_FILE = "scrape_state.json"

    def __init__(self, output_dir: str):
        logger.info("[Tracker] Initializing ProgressTracker for output_dir: %s", output_dir)
        self.output_dir = output_dir
        self.state_path = os.path.join(output_dir, self.STATE_FILE)
        logger.info("[Tracker] State file path: %s", self.state_path)
        logger.info("[Tracker] State file exists: %s", os.path.exists(self.state_path))
        self.state = self._load_state()
        logger.info("[Tracker] Loaded state: slug=%s, id=%s, title=%r, completed=%d, failed=%d",
                     self.state.get("course_slug"), self.state.get("course_id"),
                     self.state.get("course_title"), self.completed_count, self.failed_count)

    def _load_state(self) -> dict:
        """Load state from disk or create fresh state."""
        if os.path.exists(self.state_path):
            try:
                logger.info("[Tracker] Reading state from %s", self.state_path)
                with open(self.state_path, "r") as f:
                    state = json.load(f)
                logger.info("[Tracker] State loaded: %d keys", len(state))
                for k, v in state.items():
                    if k == "completed_lectures":
                        logger.debug("[Tracker]   %s: %d items", k, len(v) if isinstance(v, list) else 0)
                    elif k == "failed_lectures":
                        logger.debug("[Tracker]   %s: %d items", k, len(v) if isinstance(v, list) else 0)
                    else:
                        logger.debug("[Tracker]   %s: %s", k, str(v)[:100])
                return state
            except json.JSONDecodeError as e:
                logger.warning("[Tracker] JSON decode error reading %s: %s — creating fresh state", self.state_path, e)
            except IOError as e:
                logger.warning("[Tracker] IO error reading %s: %s — creating fresh state", self.state_path, e)
        else:
            logger.info("[Tracker] No state file found — creating fresh state")
        return {
            "course_slug": None,
            "course_id": None,
            "course_title": None,
            "total_sections": 0,
            "total_lectures": 0,
            "completed_lectures": [],
            "failed_lectures": [],
            "last_section_idx": 0,
            "last_lecture_idx": 0,
            "started_at": None,
            "last_updated": None,
            "output_dir": None,
        }

    def save(self):
        """Save current state to disk."""
        logger.debug("[Tracker] Saving state to %s", self.state_path)
        self.state["last_updated"] = time.time()
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        with open(self.state_path, "w") as f:
            json.dump(self.state, f, indent=2)
        logger.debug("[Tracker] State saved: %d completed, %d failed",
                     self.completed_count, self.failed_count)

    def init_course(self, course_slug: str, course_id: int, course_title: str,
                    total_sections: int, total_lectures: int, output_dir: str):
        """Initialize state for a new course (or resume)."""
        logger.info("[Tracker] init_course(slug=%s, id=%s, title=%r, sections=%d, lectures=%d)",
                     course_slug, course_id, course_title, total_sections, total_lectures)
        if self.state.get("course_slug") == course_slug:
            logger.info("[Tracker] Resuming same course — skipping state reset")
            return

        logger.info("[Tracker] New course — resetting state")
        self.state.update({
            "course_slug": course_slug,
            "course_id": course_id,
            "course_title": course_title,
            "total_sections": total_sections,
            "total_lectures": total_lectures,
            "completed_lectures": [],
            "failed_lectures": [],
            "last_section_idx": 0,
            "last_lecture_idx": 0,
            "started_at": time.time(),
            "last_updated": time.time(),
            "output_dir": output_dir,
        })
        self.save()

    def is_lecture_done(self, lecture_id: str) -> bool:
        """Check if a lecture has already been scraped."""
        done = lecture_id in self.state.get("completed_lectures", [])
        logger.debug("[Tracker] is_lecture_done(%s) = %s", lecture_id, done)
        return done

    def mark_lecture_done(self, lecture_id: str, section_idx: int, lecture_idx: int):
        """Mark a lecture as successfully scraped."""
        if lecture_id not in self.state["completed_lectures"]:
            self.state["completed_lectures"].append(lecture_id)
            logger.debug("[Tracker] mark_lecture_done: %s (total: %d)", lecture_id, self.completed_count)
        else:
            logger.debug("[Tracker] mark_lecture_done: %s (already marked)", lecture_id)
        self.state["last_section_idx"] = section_idx
        self.state["last_lecture_idx"] = lecture_idx
        self.save()

    def mark_lecture_failed(self, lecture_id: str, reason: str = ""):
        """Mark a lecture as failed."""
        logger.info("[Tracker] mark_lecture_failed: %s reason=%r", lecture_id, reason)
        failed = self.state.get("failed_lectures", [])
        existing = [f for f in failed if f.get("id") == lecture_id]
        if not existing:
            failed.append({"id": lecture_id, "reason": reason})
            logger.debug("[Tracker] Added failure record (total: %d)", len(failed))
        else:
            logger.debug("[Tracker] Failure already recorded for %s", lecture_id)
        self.state["failed_lectures"] = failed
        self.save()

    @property
    def completed_count(self) -> int:
        return len(self.state.get("completed_lectures", []))

    @property
    def failed_count(self) -> int:
        return len(self.state.get("failed_lectures", []))

    @property
    def is_resumable(self) -> bool:
        """Check if there's a previous session to resume."""
        slug = self.state.get("course_slug")
        started = self.state.get("started_at")
        total = self.state.get("total_lectures", 0)
        completed = self.completed_count
        resumable = slug is not None and started is not None and completed < total
        logger.debug("[Tracker] is_resumable: slug=%s, started=%s, completed=%d/%d -> %s",
                     slug, started, completed, total, resumable)
        return resumable

    def get_resume_info(self) -> dict:
        """Get info about the previous session for display."""
        if not self.is_resumable:
            return {}
        info = {
            "course_title": self.state.get("course_title", "Unknown"),
            "course_slug": self.state.get("course_slug", ""),
            "completed": self.completed_count,
            "total": self.state.get("total_lectures", 0),
            "failed": self.failed_count,
            "started_at": self.state.get("started_at"),
        }
        logger.info("[Tracker] get_resume_info: %s", info)
        return info

    def clear(self):
        """Clear all progress (start fresh)."""
        logger.info("[Tracker] clear() — resetting all progress")
        self.state = {
            "course_slug": None,
            "course_id": None,
            "course_title": None,
            "total_sections": 0,
            "total_lectures": 0,
            "completed_lectures": [],
            "failed_lectures": [],
            "last_section_idx": 0,
            "last_lecture_idx": 0,
            "started_at": None,
            "last_updated": None,
            "output_dir": None,
        }
        self.save()
