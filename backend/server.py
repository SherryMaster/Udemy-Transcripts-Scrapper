import asyncio
import re
import logging
import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from backend.scraper_session import ScraperSession
from driver import shared_manager

logger = logging.getLogger(__name__)

app = FastAPI(title="Udemy Scraper")


@app.on_event("shutdown")
async def _shutdown():
    logger.info("[Server] Shutdown event received — calling shutdown_session()")
    shutdown_session()


FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
logger.info("[Server] Mounting static files from: %s", FRONTEND_DIR)
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

_session_factory = None
app.state.session = None


def set_session_factory(factory):
    global _session_factory
    logger.info("[Server] Session factory set: %s", factory)
    _session_factory = factory


def _make_session(**kw):
    if _session_factory is not None:
        logger.info("[Server] Using custom session factory")
        return _session_factory(**kw)
    logger.info("[Server] Creating default ScraperSession()")
    return ScraperSession(**kw)


URL_RE = re.compile(r"https?://www\.udemy\.com/course/[^/]+")


@app.get("/")
async def index():
    logger.info("[Server] GET / -> serving index.html")
    return FileResponse(FRONTEND_DIR / "index.html")


@app.post("/api/start")
async def api_start(payload: dict):
    logger.info("[Server] POST /api/start")
    logger.info("[Server]   Payload: %s", {k: v for k, v in payload.items() if k != "url"})

    url = (payload.get("url") or "").strip()
    if not URL_RE.match(url):
        logger.warning("[Server] Invalid URL: %s", url)
        return JSONResponse({"error": "Invalid Udemy course URL"}, status_code=400)
    logger.info("[Server]   URL: %s", url)

    output_dir = (payload.get("outputDir") or "").strip()
    if not output_dir:
        logger.warning("[Server] Missing outputDir")
        return JSONResponse({"error": "outputDir is required"}, status_code=400)
    logger.info("[Server]   Output dir: %s", output_dir)

    batch_size = int(payload.get("batchSize", 5))
    num_threads = int(payload.get("numThreads", 3))
    logger.info("[Server]   batch_size=%d, num_threads=%d", batch_size, num_threads)

    logger.info("[Server] Creating new ScraperSession...")
    app.state.session = _make_session()
    logger.info("[Server] Starting scrape...")
    app.state.session.start(url, output_dir, batch_size, num_threads, resume=False)

    return JSONResponse({"status": "started"}, status_code=202)


@app.post("/api/resume")
async def api_resume(payload: dict):
    logger.info("[Server] POST /api/resume")
    output_dir = (payload.get("outputDir") or "").strip()
    if not output_dir:
        logger.warning("[Server] Missing outputDir for resume")
        return JSONResponse({"error": "outputDir is required"}, status_code=400)
    logger.info("[Server]   Output dir: %s", output_dir)

    logger.info("[Server] Creating new ScraperSession for resume...")
    app.state.session = _make_session()
    logger.info("[Server] Starting resume...")
    app.state.session.start("", output_dir, 5, 3, resume=True)

    return JSONResponse({"status": "resumed"}, status_code=202)


@app.post("/api/stop")
async def api_stop():
    logger.info("[Server] POST /api/stop")
    if app.state.session:
        logger.info("[Server] Calling session.stop()...")
        app.state.session.stop()
    else:
        logger.warning("[Server] No active session to stop")
    return JSONResponse({"status": "stopping"}, status_code=202)


@app.post("/api/retry-failed")
async def api_retry():
    logger.info("[Server] POST /api/retry-failed")
    if app.state.session:
        logger.info("[Server] Calling session.retry_failed()...")
        app.state.session.retry_failed()
    else:
        logger.warning("[Server] No active session for retry")
    return JSONResponse({"status": "retrying"}, status_code=202)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    logger.info("[Server] WebSocket connected: %s", ws.client)
    await ws.accept()
    logger.info("[Server] WebSocket accepted, waiting for session...")

    while app.state.session is None:
        try:
            await asyncio.wait_for(ws.receive_text(), timeout=0.5)
        except asyncio.TimeoutError:
            pass
        except WebSocketDisconnect:
            logger.info("[Server] WebSocket disconnected while waiting for session")
            return

    logger.info("[Server] Session found, streaming events to WebSocket...")
    try:
        async for event in app.state.session.events():
            logger.debug("[Server] WS sending event type=%s", event.get("type"))
            await ws.send_json(event)
        logger.info("[Server] Event stream ended")
    except WebSocketDisconnect:
        logger.info("[Server] WebSocket disconnected during event stream")
        return


def shutdown_session():
    logger.info("[Server] shutdown_session() called")
    if app.state.session:
        is_running = getattr(app.state.session, "is_running", False)
        logger.info("[Server] Session exists, is_running=%s", is_running)
        if is_running:
            app.state.session.stop()
    else:
        logger.info("[Server] No session to shut down")
    logger.info("[Server] Quitting shared_manager...")
    shared_manager.quit()
    logger.info("[Server] shutdown_session() complete")
