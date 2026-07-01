"""Desktop app window for agent-core — a small, collapsible native window (no browser).

Runs the server in-process and shows the built-in chat + routines UI in a compact native
window via pywebview. The UI can fold to a slim bar and expand back (the window resizes with
it via the tiny JS API below), and the layout is responsive. Falls back to the browser if
pywebview isn't installed.

Run:    python -m agent_core.app
Extras: pip install pywebview
"""
from __future__ import annotations

import asyncio
import threading
import time
import urllib.request
import webbrowser

from agent_core import config

URL = f"http://{config.HOST}:{config.PORT}"

# Window sizes for the collapse/expand toggle (used by the JS API from the UI).
EXPANDED = (460, 640)
COLLAPSED = (460, 140)


def _serve() -> None:
    """Run uvicorn in this (non-main) thread without touching signal handlers."""
    import uvicorn
    from agent_core.runtime.server import app as fastapi_app

    server = uvicorn.Server(uvicorn.Config(fastapi_app, host=config.HOST, port=config.PORT, log_level="warning"))
    server.install_signal_handlers = lambda: None  # signals only work on the main thread
    asyncio.run(server.serve())


def _wait_healthy(timeout: int = 40) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(URL + "/health", timeout=2)
            return True
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    return False


class _Api:
    """Exposed to the page as pywebview.api — lets the UI resize its own window."""
    def __init__(self) -> None:
        self.window = None

    def resize(self, w: int, h: int) -> None:
        try:
            if self.window is not None:
                self.window.resize(int(w), int(h))
        except Exception:  # noqa: BLE001
            pass


def main() -> None:
    threading.Thread(target=_serve, daemon=True).start()
    _wait_healthy()

    try:
        import webview  # pywebview
    except Exception:  # noqa: BLE001 — not installed: fall back to the browser
        print(f"[agent-core] pywebview not installed — opening in your browser at {URL}.")
        print("[agent-core] `pip install pywebview` for the native window. Ctrl+C to stop.")
        webbrowser.open(URL)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return

    api = _Api()
    api.window = webview.create_window(
        "agent-core", URL,
        width=EXPANDED[0], height=EXPANDED[1],
        min_size=(340, 130),
        js_api=api,
    )
    webview.start()   # blocks until the window is closed; the daemon server thread exits with it


if __name__ == "__main__":
    main()
