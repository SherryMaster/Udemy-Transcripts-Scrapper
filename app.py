import threading
import time
import urllib.request

import uvicorn

from backend.server import app, shutdown_session

HOST = "127.0.0.1"
PORT = 8765


def _wait_for_server(url, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=0.5)
            return True
        except Exception:
            time.sleep(0.15)
    return False


def _run_server():
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


def main():
    server_thread = threading.Thread(target=_run_server, daemon=True)
    server_thread.start()

    url = f"http://{HOST}:{PORT}/"
    if not _wait_for_server(url):
        print("Failed to start local server. Aborting.")
        return

    print(f"Udemy Transcript Scraper running at {url}")
    print("Press Ctrl+C to stop.")

    try:
        import webview

        webview.create_window(
            "Udemy Transcript Scraper", url,
            width=1040, height=720, min_size=(800, 600),
        )
        webview.start()
    except Exception:
        import webbrowser
        webbrowser.open(url)
        while True:
            time.sleep(1)

    shutdown_session()


if __name__ == "__main__":
    main()
