import logging
import os
import sys
import threading
import time
import traceback
import urllib.request

import uvicorn

from backend.server import app, shutdown_session

# ---------------------------------------------------------------------------
# LOGGING SETUP
# Log everything to stderr so the terminal is the single source of truth
# for all diagnostic information.
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("selenium").setLevel(logging.WARNING)
logging.getLogger("selenium.webdriver").setLevel(logging.WARNING)
logging.getLogger("selenium.remote").setLevel(logging.WARNING)
logging.getLogger("selenium.webdriver.remote").setLevel(logging.WARNING)
logging.getLogger("selenium.webdriver.common").setLevel(logging.WARNING)
logging.getLogger("uvicorn").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("uvicorn.error").setLevel(logging.INFO)
logging.getLogger("webview").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
PORT = 8765


def _wait_for_server(url, timeout=15):
    logger.info("[App] Waiting for server at %s (timeout=%ds)...", url, timeout)
    deadline = time.time() + timeout
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            urllib.request.urlopen(url, timeout=0.5)
            logger.info("[App] Server ready after %d attempts (%.2fs)", attempt, time.time() - (deadline - timeout))
            return True
        except Exception as e:
            if attempt % 10 == 1:
                logger.debug("[App] Attempt %d: server not ready yet: %s", attempt, type(e).__name__)
            time.sleep(0.15)
    logger.error("[App] Server did not start within %ds", timeout)
    return False


def _run_server():
    logger.info("[App] Starting uvicorn server on %s:%d...", HOST, PORT)
    try:
        uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
    except Exception as e:
        logger.error("[App] uvicorn crashed: %s: %s", type(e).__name__, e)
        logger.error(traceback.format_exc())


def main():
    logger.info("[App] ===== Udemy Transcript Scraper =====")
    logger.info("[App] PID: %d", os.getpid())
    logger.info("[App] CWD: %s", os.getcwd())
    logger.info("[App] Python: %s", sys.executable)
    logger.info("[App] Python version: %s", sys.version)
    logger.info("[App] Platform: %s", sys.platform)

    # Log key environment variables
    logger.info("[App] DISPLAY=%s", os.environ.get("DISPLAY", "(not set)"))
    logger.info("[App] WAYLAND_DISPLAY=%s", os.environ.get("WAYLAND_DISPLAY", "(not set)"))
    logger.info("[App] XDG_SESSION_TYPE=%s", os.environ.get("XDG_SESSION_TYPE", "(not set)"))
    logger.info("[App] HOME=%s", os.environ.get("HOME", "(not set)"))
    logger.info("[App] PATH=%s", os.environ.get("PATH", "(not set)")[:300])

    logger.info("[App] Starting server thread...")
    server_thread = threading.Thread(target=_run_server, daemon=True, name="ServerThread")
    server_thread.start()

    url = f"http://{HOST}:{PORT}/"
    if not _wait_for_server(url):
        logger.error("[App] Failed to start local server. Aborting.")
        print("Failed to start local server. Aborting.", file=sys.stderr)
        return

    print(f"Udemy Transcript Scraper running at {url}", file=sys.stderr)
    print("Press Ctrl+C to stop.", file=sys.stderr)

    try:
        logger.info("[App] Importing webview...")
        import webview

        logger.info("[App] Creating webview window: 'Udemy Transcript Scraper' (%dx%d, min %dx%d)",
                     1040, 720, 800, 600)
        webview.create_window(
            "Udemy Transcript Scraper", url,
            width=1040, height=720, min_size=(800, 600),
        )
        logger.info("[App] Starting webview (blocking)...")
        webview.start()
        logger.info("[App] Webview exited")
    except ImportError:
        logger.warning("[App] pywebview not installed — falling back to webbrowser")
        import webbrowser
        webbrowser.open(url)
        logger.info("[App] Browser opened, waiting indefinitely...")
        while True:
            time.sleep(1)
    except Exception as e:
        logger.error("[App] Webview failed: %s: %s", type(e).__name__, e)
        logger.error(traceback.format_exc())
        import webbrowser
        webbrowser.open(url)
        while True:
            time.sleep(1)

    logger.info("[App] Shutting down...")
    shutdown_session()
    logger.info("[App] Goodbye")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("[App] Ctrl+C — exiting")
        print("\nExiting.", file=sys.stderr)
    except Exception as e:
        logger.error("[App] Unhandled exception: %s: %s", type(e).__name__, e)
        logger.error(traceback.format_exc())
        sys.exit(1)
