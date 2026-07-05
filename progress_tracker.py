"""
Progress Tracker for Udemy Transcript Scraper
Saves and loads scraping progress so it can resume after interruption.
"""
import json
import os
import time


class ProgressTracker:
    """Tracks and persists scraping progress for resume capability."""

    STATE_FILE = "scrape_state.json"

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        self.state_path = os.path.join(output_dir, self.STATE_FILE)
        self.state = self._load_state()

    def _load_state(self) -> dict:
        """Load state from disk or create fresh state."""
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
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
        self.state["last_updated"] = time.time()
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        with open(self.state_path, "w") as f:
            json.dump(self.state, f, indent=2)

    def init_course(self, course_slug: str, course_id: int, course_title: str,
                    total_sections: int, total_lectures: int, output_dir: str):
        """Initialize state for a new course (or resume)."""
        if self.state.get("course_slug") == course_slug:
            # Resuming same course
            return

        # New course
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
        return lecture_id in self.state.get("completed_lectures", [])

    def mark_lecture_done(self, lecture_id: str, section_idx: int, lecture_idx: int):
        """Mark a lecture as successfully scraped."""
        if lecture_id not in self.state["completed_lectures"]:
            self.state["completed_lectures"].append(lecture_id)
        self.state["last_section_idx"] = section_idx
        self.state["last_lecture_idx"] = lecture_idx
        self.save()

    def mark_lecture_failed(self, lecture_id: str, reason: str = ""):
        """Mark a lecture as failed."""
        failed = self.state.get("failed_lectures", [])
        # Avoid duplicates
        existing = [f for f in failed if f.get("id") == lecture_id]
        if not existing:
            failed.append({"id": lecture_id, "reason": reason})
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
        return (
            self.state.get("course_slug") is not None
            and self.state.get("started_at") is not None
            and self.completed_count < self.state.get("total_lectures", 0)
        )

    def get_resume_info(self) -> dict:
        """Get info about the previous session for display."""
        if not self.is_resumable:
            return {}
        return {
            "course_title": self.state.get("course_title", "Unknown"),
            "course_slug": self.state.get("course_slug", ""),
            "completed": self.completed_count,
            "total": self.state.get("total_lectures", 0),
            "failed": self.failed_count,
            "started_at": self.state.get("started_at"),
        }

    def clear(self):
        """Clear all progress (start fresh)."""
        self.state = self._load_state.__func__(self)
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
