import json
import os
import time
from pathlib import Path

from progress_tracker import ProgressTracker


def test_init_new_course(tmp_path):
    t = ProgressTracker(str(tmp_path))
    t.init_course("my-course", 123, "My Course", 4, 40, str(tmp_path))
    assert t.state["course_slug"] == "my-course"
    assert t.state["total_lectures"] == 40
    assert t.completed_count == 0
    assert os.path.exists(t.state_path)


def test_init_same_course_does_not_reset(tmp_path):
    t = ProgressTracker(str(tmp_path))
    t.init_course("my-course", 123, "My Course", 4, 40, str(tmp_path))
    t.mark_lecture_done("lec-1", 0, 0)
    t.init_course("my-course", 123, "My Course", 4, 40, str(tmp_path))  # resume
    assert t.is_lecture_done("lec-1") is True


def test_mark_done_and_failed(tmp_path):
    t = ProgressTracker(str(tmp_path))
    t.init_course("c", 1, "C", 1, 3, str(tmp_path))
    t.mark_lecture_done("a", 0, 0)
    t.mark_lecture_failed("b", "no_captions")
    assert t.completed_count == 1
    assert t.failed_count == 1
    assert t.is_lecture_done("a") is True


def test_is_resumable(tmp_path):
    t = ProgressTracker(str(tmp_path))
    assert t.is_resumable is False
    t.init_course("c", 1, "C", 1, 5, str(tmp_path))
    assert t.is_resumable is True
    for i, lid in enumerate(["l1", "l2", "l3", "l4", "l5"]):
        t.mark_lecture_done(lid, 0, i)
    assert t.is_resumable is False  # all done


def test_corrupted_state_file_falls_back_to_fresh(tmp_path):
    state_path = Path(tmp_path) / "scrape_state.json"
    state_path.write_text("{ not valid json ")
    t = ProgressTracker(str(tmp_path))
    assert t.state["course_slug"] is None
    assert t.state["total_lectures"] == 0


def test_resume_info(tmp_path):
    t = ProgressTracker(str(tmp_path))
    t.init_course("c", 1, "C", 2, 10, str(tmp_path))
    t.mark_lecture_done("l1", 0, 0)
    info = t.get_resume_info()
    assert info["course_slug"] == "c"
    assert info["completed"] == 1
    assert info["total"] == 10
