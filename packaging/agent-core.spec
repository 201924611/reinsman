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

# collect_all misses the SDK's large bundled CLI (_bundled/claude.exe ~235MB) — add it explicitly
# so the packaged app can actually run agents. It must land at claude_agent_sdk/_bundled/ at runtime.
try:
    import claude_agent_sdk
    _sdk_bundled = os.path.join(os.path.dirname(claude_agent_sdk.__file__), "_bundled")
    if os.path.isdir(_sdk_bundled):
        datas.append((_sdk_bundled, os.path.join("claude_agent_sdk", "_bundled")))
except Exception:
    pass

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
