import threading
import time
import urllib.request

import uvicorn
import webview

from backend.server import app, shutdown_session

HOST = "127.0.0.1"
PORT = 8765


class JsApi:
    def browse_directory(self):
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        path = filedialog.askdirectory(title="Select Output Directory")
        root.destroy()
        return path or ""


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

    webview.create_window(
        "Udemy Transcript Scraper", url,
        width=1040, height=720, min_size=(800, 600),
        js_api=JsApi(),
    )
    webview.start()
    shutdown_session()


if __name__ == "__main__":
    main()
