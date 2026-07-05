import asyncio
from backend.scraper_session import normalize_status, ScraperSession


def test_normalize_status_mapping():
    assert normalize_status("working") == "in-progress"
    assert normalize_status("saved") == "success"
    assert normalize_status("skipped") == "skipped"
    assert normalize_status("failed") == "failed"
    assert normalize_status("unknown") == "failed"


def test_session_wraps_callback_and_normalizes():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    session = ScraperSession(loop=loop)

    loop.call_soon_threadsafe = lambda f, *a: f(*a)
    session._on_scraper_event({"type": "lecture_status", "sectionIdx": 0, "lectureIdx": 2,
                               "status": "saved", "message": "Saved: X", "size": 99})

    async def drain():
        while session.queue.empty():
            await asyncio.sleep(0)
        return session.queue.get_nowait()

    ev = loop.run_until_complete(drain())
    assert ev["type"] == "lecture_status"
    assert ev["status"] == "success"
    assert ev["size"] == 99
    loop.close()
