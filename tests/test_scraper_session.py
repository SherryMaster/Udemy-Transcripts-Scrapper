from backend.scraper_session import normalize_status, ScraperSession


def test_normalize_status_mapping():
    assert normalize_status("working") == "in-progress"
    assert normalize_status("saved") == "success"
    assert normalize_status("skipped") == "skipped"
    assert normalize_status("failed") == "failed"
    assert normalize_status("unknown") == "failed"


def test_session_wraps_callback_and_normalizes():
    session = ScraperSession()
    session._on_scraper_event({"type": "lecture_status", "sectionIdx": 0, "lectureIdx": 2,
                                "status": "saved", "message": "Saved: X", "size": 99})
    # also emits progress, so drain two events
    ev1 = session._tqueue.get(timeout=1)
    ev2 = session._tqueue.get(timeout=1)
    # first event is lecture_status
    ev = ev1 if ev1["type"] == "lecture_status" else ev2
    assert ev["type"] == "lecture_status"
    assert ev["status"] == "success"
    assert ev["size"] == 99
