"""Desktop app window for agent-core — a real program window, no browser.

Starts the server in the background and shows the built-in chat + routines UI in a
**native window** via pywebview (on Windows it uses the built-in Edge WebView2).
If pywebview isn't installed, it falls back to opening the UI in your browser.

Run:    python -m agent_core.app
Extras: pip install pywebview        # for the native window
"""
from __future__ import annotations

import subprocess
import sys
import time
import urllib.request
import webbrowser

from agent_core import config

URL = f"http://{config.HOST}:{config.PORT}"
_proc: subprocess.Popen | None = None


def _start_server() -> None:
    global _proc
    _proc = subprocess.Popen([sys.executable, "-m", "agent_core"], cwd=str(config.ROOT))


def _stop_server() -> None:
    if _proc and _proc.poll() is None:
        _proc.terminate()
        try:
            _proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            _proc.kill()


def _wait_healthy(timeout: int = 40) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(URL + "/health", timeout=2)
            return True
        except Exception:  # noqa: BLE001
            time.sleep(1)
    return False


def main() -> None:
    _start_server()
    try:
        import webview  # pywebview
    except Exception:  # noqa: BLE001 — not installed: fall back to the browser
        print(f"[agent-core] pywebview not installed — opening in your browser at {URL}.")
        print("[agent-core] `pip install pywebview` for a native app window. Ctrl+C to stop.")
        _wait_healthy()
        webbrowser.open(URL)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            _stop_server()
        return

    _wait_healthy()
    webview.create_window("agent-core", URL, width=980, height=760, min_size=(680, 520))
    try:
        webview.start()          # blocks until the window is closed
    finally:
        _stop_server()


if __name__ == "__main__":
    main()
