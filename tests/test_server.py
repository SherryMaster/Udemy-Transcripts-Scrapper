import pytest
from fastapi.testclient import TestClient

from backend.server import app, set_session_factory


class FakeSession:
    def __init__(self, **kw):
        self.started = False
        self.stopped = False
        self.retried = False

    def start(self, url, output_dir, batch_size, num_threads, resume=False):
        self.started = True

    def stop(self):
        self.stopped = True

    def retry_failed(self):
        self.retried = True
        return []

    async def events(self):
        yield {"type": "course_discovered", "courseTitle": "Fake",
               "sections": [{"index": 1, "title": "S1", "lectures": [{"index": 0, "id": "l1", "title": "L1"}]}]}
        yield {"type": "lecture_status", "sectionIdx": 0, "lectureIdx": 0,
               "status": "success", "message": "Saved", "size": 10}
        yield {"type": "progress", "completed": 1, "total": 1, "failed": 0,
               "skipped": 0, "active": 0, "success": 1, "elapsedMs": 0}
        yield {"type": "done", "completed": 1, "total": 1, "failed": 0,
               "skipped": 0, "active": 0, "success": 1, "elapsedMs": 0}


@pytest.fixture(autouse=True)
def fake_factory():
    set_session_factory(lambda **kw: FakeSession())
    yield
    set_session_factory(None)
    app.state.session = None


def test_start_returns_202():
    client = TestClient(app)
    r = client.post("/api/start", json={"url": "https://www.udemy.com/course/x/learn",
                                        "outputDir": "/tmp/out", "batchSize": 5, "numThreads": 3})
    assert r.status_code == 202


def test_start_validates_url():
    client = TestClient(app)
    r = client.post("/api/start", json={"url": "not-a-url", "outputDir": "/tmp/out",
                                        "batchSize": 5, "numThreads": 3})
    assert r.status_code == 400
    assert "url" in r.json()["error"].lower()


def test_resume_missing_output_dir():
    client = TestClient(app)
    r = client.post("/api/resume", json={})
    assert r.status_code == 400


def test_stop_and_retry():
    client = TestClient(app)
    client.post("/api/start", json={"url": "https://www.udemy.com/course/x/learn",
                                    "outputDir": "/tmp/out", "batchSize": 5, "numThreads": 3})
    assert client.post("/api/stop").status_code == 202
    assert client.post("/api/retry-failed").status_code == 202


def test_ws_feeds_events_in_order():
    client = TestClient(app)
    client.post("/api/start", json={"url": "https://www.udemy.com/course/x/learn",
                                    "outputDir": "/tmp/out", "batchSize": 5, "numThreads": 3})
    with client.websocket_connect("/ws") as ws:
        ev = ws.receive_json()
        assert ev["type"] == "course_discovered"
        ev = ws.receive_json()
        assert ev["type"] == "lecture_status" and ev["status"] == "success"
        ev = ws.receive_json()
        assert ev["type"] == "progress"
        ev = ws.receive_json()
        assert ev["type"] == "done"
