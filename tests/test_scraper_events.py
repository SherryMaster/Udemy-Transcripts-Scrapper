import json
from unittest.mock import patch

from scraper import UdemyScraper


def _make_scraper_with_sections():
    s = UdemyScraper()
    s.course_id = 999
    s.output_dir = "/tmp/test-out"
    s.sections = [
        {"index": 1, "id": "sec1", "title": "Intro", "folder_name": "01_Intro",
         "lectures": [
             {"id": "l1", "title": "Welcome"},
             {"id": "l2", "title": "Quiz 1"},
             {"id": "l3", "title": "Setup"},
         ]},
    ]
    return s


def test_scraper_emits_event_dicts_per_status():
    s = _make_scraper_with_sections()
    events = []

    fake_results = json.dumps({
        "l1": {"s": "ok", "t": "hello world"},
        "l2": {"s": "api_error"},          # quiz -> skipped
        "l3": {"s": "no_captions"},         # -> skipped
    })

    with patch("scraper.run_browser_use", return_value=fake_results), \
         patch("scraper.UdemyScraper.save_transcript"):
        s.scrape_parallel(
            base_dir="/tmp/test-out",
            progress_callback=events.append,
            stop_check=lambda: False,
            batch_size=5,
            num_threads=1,
            skip_discovery=True,
        )

    lecture_events = [e for e in events if e.get("type") == "lecture_status"]
    statuses = {(e["sectionIdx"], e["lectureIdx"]): e["status"] for e in lecture_events}
    assert statuses[(0, 0)] == "saved"        # l1 ok
    assert statuses[(0, 1)] == "skipped"      # l2 api_error
    assert statuses[(0, 2)] == "skipped"      # l3 no_captions

    working = [e for e in lecture_events if e["status"] == "working"]
    assert len(working) == 3                   # one working event per lecture in batch

    assert any(e.get("type") == "scrape_finished" for e in events)


def test_scraper_failed_event_on_error_status():
    s = _make_scraper_with_sections()
    events = []
    fake_results = json.dumps({"l1": {"s": "vtt_error"}, "l2": {"s": "error", "m": "boom"}, "l3": {"s": "empty"}})
    with patch("scraper.run_browser_use", return_value=fake_results), \
         patch("scraper.UdemyScraper.save_transcript"):
        s.scrape_parallel("/tmp/test-out", events.append, lambda: False, 5, 1, skip_discovery=True)
    fails = [e for e in events if e.get("type") == "lecture_status" and e["status"] == "failed"]
    assert len(fails) == 3


def test_scraper_stop_check_aborts():
    s = _make_scraper_with_sections()
    events = []
    fake_results = json.dumps({"l1": {"s": "ok", "t": "hi"}})
    stop_after = [0]

    def stop_check():
        stop_after[0] += 1
        return stop_after[0] > 1   # stop after first check passes

    with patch("scraper.run_browser_use", return_value=fake_results), \
         patch("scraper.UdemyScraper.save_transcript"):
        s.scrape_parallel("/tmp/test-out", events.append, stop_check, 1, 1, skip_discovery=True)

    saved = [e for e in events if e.get("type") == "lecture_status" and e["status"] == "saved"]
    assert len(saved) <= 1   # did not process all 3 lectures
