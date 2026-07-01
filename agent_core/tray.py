"""Desktop 'app' launcher for agent-core.

Runs the server (`python -m agent_core`) as a child process, opens the built-in web
chat in your browser, and — if `pystray` + `pillow` are installed — shows a tray icon
with Open / Restart / Quit. Without those optional deps it still starts the server and
opens the browser, staying up until Ctrl+C.

Run:    python -m agent_core.tray
Extras: pip install pystray pillow      # optional, for the tray icon
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

from agent_core import config

URL = f"http://{config.HOST}:{config.PORT}"
_proc: subprocess.Popen | None = None


def _start() -> None:
    global _proc
    if _proc and _proc.poll() is None:
        return
    _proc = subprocess.Popen([sys.executable, "-m", "agent_core"], cwd=str(config.ROOT))


def _stop() -> None:
    if _proc and _proc.poll() is None:
        _proc.terminate()
        try:
            _proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            _proc.kill()


def _wait_healthy(timeout: int = 30) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(URL + "/health", timeout=2)
            return True
        except Exception:  # noqa: BLE001
            time.sleep(1)
    return False


def _open() -> None:
    webbrowser.open(URL)


def main() -> None:
    _start()
    threading.Thread(target=lambda: _wait_healthy() and _open(), daemon=True).start()

    try:
        from PIL import Image, ImageDraw
        from pystray import Icon, Menu, MenuItem
    except Exception:  # noqa: BLE001 — optional deps missing: run headless
        print(f"[agent-core] server starting at {URL}")
        print("[agent-core] tray icon disabled — `pip install pystray pillow` to enable. Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            _stop()
        return

    img = Image.new("RGB", (64, 64), (22, 27, 34))
    ImageDraw.Draw(img).ellipse((18, 18, 46, 46), fill=(46, 160, 67))

    def _restart(icon, item):  # noqa: ANN001
        _stop()
        _start()

    def _quit(icon, item):  # noqa: ANN001
        _stop()
        icon.stop()

    menu = Menu(
        MenuItem("Open chat", lambda icon, item: _open()),
        MenuItem("Restart server", _restart),
        MenuItem("Quit", _quit),
    )
    Icon("agent-core", img, "agent-core", menu).run()


if __name__ == "__main__":
    main()
