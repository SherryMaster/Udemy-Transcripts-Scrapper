from unittest.mock import patch, MagicMock

import pytest

from backend.scraper_session import normalize_status, ScraperSession
from scraper import UdemyScraper


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


def _make_scraper_with_sections():
    s = UdemyScraper(log_callback=lambda m: None)
    s.course_id = 123
    s.course_title = "Test Course"
    s.output_dir = "/tmp/out"
    s.sections = [
        {"index": 1, "id": "sec1", "title": "S1", "folder_name": "01_S1",
         "lectures": [{"id": "l1", "title": "L1"}, {"id": "l2", "title": "L2"}]}
    ]
    return s


@patch("scraper.shared_manager")
def test_sequential_batch_loop_emits_saved_events(mock_mgr):
    mock_mgr.execute_async_js.return_value = '{"l1": {"s":"ok","t":"hello world"}, "l2": {"s":"ok","t":"foo bar"}}'

    s = _make_scraper_with_sections()
    s.save_transcript = MagicMock(return_value="/tmp/fake")

    events = []
    s.scrape_parallel(
        base_dir="/tmp/out",
        progress_callback=lambda ev: events.append(ev),
        stop_check=lambda: False,
        batch_size=10,
        skip_discovery=True,
    )

    statuses = [e["status"] for e in events if e.get("type") == "lecture_status"]
    assert "working" in statuses
    assert statuses.count("saved") == 2
    finished = [e for e in events if e.get("type") == "scrape_finished"]
    assert len(finished) == 1
    assert finished[0]["completed"] == 2
    assert finished[0]["failed"] == 0


@patch("scraper.shared_manager")
def test_stop_check_halts_loop_mid_batch(mock_mgr):
    mock_mgr.execute_async_js.return_value = '{}'

    s = _make_scraper_with_sections()
    events = []
    call_count = [0]

    def stop_check():
        call_count[0] += 1
        return call_count[0] > 1

    s.scrape_parallel(
        base_dir="/tmp/out",
        progress_callback=lambda ev: events.append(ev),
        stop_check=stop_check,
        batch_size=1,
        skip_discovery=True,
    )

    # Only l1 (lectureIdx 0) got a "working" event; l2 (lectureIdx 1) was never started.
    working = [e for e in events if e.get("type") == "lecture_status" and e["status"] == "working"]
    assert len(working) == 1
    assert working[0]["lectureIdx"] == 0
    # Only one lecture was processed (l1), so completed+failed < total (2).
    finished = [e for e in events if e.get("type") == "scrape_finished"]
    assert len(finished) == 1
    assert finished[0]["completed"] + finished[0]["failed"] == 1


@patch("scraper.shared_manager")
def test_no_captions_status_is_skipped(mock_mgr):
    mock_mgr.execute_async_js.return_value = '{"l1": {"s":"no_captions"}, "l2": {"s":"error"}}'

    s = _make_scraper_with_sections()

    events = []
    s.scrape_parallel(
        base_dir="/tmp/out",
        progress_callback=lambda ev: events.append(ev),
        stop_check=lambda: False,
        batch_size=10,
        skip_discovery=True,
    )

    statuses = [e["status"] for e in events if e.get("type") == "lecture_status"]
    assert "skipped" in statuses
    assert "failed" in statuses
    finished = [e for e in events if e.get("type") == "scrape_finished"]
    assert finished[0]["completed"] == 1
    assert finished[0]["failed"] == 1


@patch("scraper.shared_manager")
def test_js_json_raises_on_error_envelope(mock_mgr):
    mock_mgr.execute_async_js.return_value = '{"error": "Failed to fetch"}'
    s = UdemyScraper(log_callback=lambda m: None)
    with pytest.raises(RuntimeError, match="JS error: Failed to fetch"):
        s._js_json("some js")


@patch("scraper.shared_manager")
def test_js_json_returns_parsed_on_success(mock_mgr):
    mock_mgr.execute_async_js.return_value = '{"id": 123, "title": "Course"}'
    s = UdemyScraper(log_callback=lambda m: None)
    out = s._js_json("some js")
    assert out == {"id": 123, "title": "Course"}
