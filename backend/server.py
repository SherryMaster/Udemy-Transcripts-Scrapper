import re

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from backend.scraper_session import ScraperSession

app = FastAPI(title="Udemy Scraper")


@app.on_event("shutdown")
async def _shutdown():
    shutdown_session()

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

_session_factory = None
app.state.session = None


def set_session_factory(factory):
    global _session_factory
    _session_factory = factory


def _make_session(**kw):
    if _session_factory is not None:
        return _session_factory(**kw)
    return ScraperSession(**kw)


URL_RE = re.compile(r"https?://www\.udemy\.com/course/[^/]+")


@app.get("/")
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.post("/api/start")
async def api_start(payload: dict):
    url = (payload.get("url") or "").strip()
    if not URL_RE.match(url):
        return JSONResponse({"error": "Invalid Udemy course URL"}, status_code=400)
    output_dir = (payload.get("outputDir") or "").strip()
    if not output_dir:
        return JSONResponse({"error": "outputDir is required"}, status_code=400)
    batch_size = int(payload.get("batchSize", 5))
    num_threads = int(payload.get("numThreads", 3))

    app.state.session = _make_session()
    app.state.session.start(url, output_dir, batch_size, num_threads, resume=False)
    return JSONResponse({"status": "started"}, status_code=202)


@app.post("/api/resume")
async def api_resume(payload: dict):
    output_dir = (payload.get("outputDir") or "").strip()
    if not output_dir:
        return JSONResponse({"error": "outputDir is required"}, status_code=400)
    app.state.session = _make_session()
    app.state.session.start("", output_dir, 5, 3, resume=True)
    return JSONResponse({"status": "resumed"}, status_code=202)


@app.post("/api/stop")
async def api_stop():
    if app.state.session:
        app.state.session.stop()
    return JSONResponse({"status": "stopping"}, status_code=202)


@app.post("/api/retry-failed")
async def api_retry():
    if app.state.session:
        app.state.session.retry_failed()
    return JSONResponse({"status": "retrying"}, status_code=202)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    session = app.state.session
    if session is None:
        await ws.send_json({"type": "log", "message": "Waiting for a scrape to start...", "level": "info"})
        return
    try:
        async for event in session.events():
            await ws.send_json(event)
    except WebSocketDisconnect:
        return


def shutdown_session():
    if app.state.session and getattr(app.state.session, "is_running", False):
        app.state.session.stop()
