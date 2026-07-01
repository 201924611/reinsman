# PyInstaller spec — single-file agent-core desktop app (native window).
# Build:  pyinstaller packaging/agent-core.spec --noconfirm    (see packaging/build_exe.ps1)
# Produces dist/agent-core.exe. On first run it seeds ~/.agent-core with templates/agents/
# knowledge/.env (see config._seed); each user still authenticates once.
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.abspath(os.path.join(os.path.dirname(SPEC), ".."))

datas, binaries, hiddenimports = [], [], []
# Bundle third-party packages that ship data/binaries (claude_agent_sdk bundles claude.exe).
for pkg in ("claude_agent_sdk", "webview", "uvicorn", "fastapi", "starlette",
            "pydantic", "pydantic_core", "dotenv", "anyio", "httpx", "httptools", "websockets"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception:
        pass
hiddenimports += collect_submodules("uvicorn")

# NOTE: Anthropic's proprietary Claude Code CLI (claude_agent_sdk/_bundled/claude.exe) is
# intentionally NOT bundled — it is not ours to ship. This .exe requires Claude to be present at
# runtime (an ANTHROPIC_API_KEY, or the Claude Code CLI on PATH). For most use, run from source /
# the app launcher instead. collect_all above deliberately does not pull the bundled CLI binary.

# Bundle the read-only assets the app seeds into the data home on first run.
for sub in ("templates", "agents", "knowledge"):
    p = os.path.join(ROOT, sub)
    if os.path.isdir(p):
        datas.append((p, sub))
if os.path.isfile(os.path.join(ROOT, ".env.example")):
    datas.append((os.path.join(ROOT, ".env.example"), "."))

a = Analysis(
    [os.path.join(ROOT, "agent_core", "app.py")],
    pathex=[ROOT],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="agent-core",
    console=False,           # windowed app (no console)
    onefile=True,
    upx=False,
)
