"""Desktop app window for agent-core — a real program window, no browser.

Runs the server **in-process** (background thread) and shows the built-in chat + routines
UI in a native window via pywebview (Windows uses the built-in Edge WebView2). If pywebview
isn't installed, it falls back to opening the UI in your browser.

Works both from source (`python -m agent_core.app`) and as a packaged PyInstaller .exe.
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


def main() -> None:
    threading.Thread(target=_serve, daemon=True).start()
    _wait_healthy()

    try:
        import webview  # pywebview
    except Exception:  # noqa: BLE001 — not installed: fall back to the browser
        print(f"[agent-core] pywebview not installed — opening in your browser at {URL}.")
        print("[agent-core] `pip install pywebview` for a native app window. Ctrl+C to stop.")
        webbrowser.open(URL)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return

    webview.create_window("agent-core", URL, width=980, height=760, min_size=(680, 520))
    webview.start()   # blocks until the window is closed; the daemon server thread exits with it


if __name__ == "__main__":
    main()
