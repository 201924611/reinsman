# agent-core

> A self-hosted **agent harness** — throw one HTTP *goal* at it and a 24/7 central
> orchestrator (Claude, opus) carries it out autonomously, spawning sub-agents and
> iterating on builds, while persisting what it learns into a local Obsidian-style
> knowledge graph.

**Agent = Model + Harness.** This repo is the *harness*: the loop, tool dispatch,
context management, memory, observability, and safety scaffolding around the model.

```
HTTP POST /goal  ──▶  central orchestrator (agent_core/runtime/orchestrator.py)
                        │  spawn_agent / build_loop / spawn_parallel
                        ├──▶ sub-agent ─(done)→ runtime md auto-deleted
                        ├──▶ sub-agent ─(done)→ ...
                        └──▶ ...
                     results → task_store(JSON) + traces/ + knowledge/
HTTP GET /tasks/{id} ──▶ progress / result
```

## Features
- **Central orchestrator loop** with auto-resume on turn exhaustion (`agent_core/runtime/orchestrator.py`)
- **Dynamic sub-agents** from cited prompt templates (`agent_core/runtime/agent_factory.py`, `templates/`)
- **`build_loop`** — planner → executor → evaluator iteration with best-round snapshot restore
- **Persistent knowledge graph** — `save_knowledge` writes an Obsidian-compatible vault
  (`knowledge/`, `[[wikilinks]]` + auto Index/Graph)
- **Channels** — chat front via messengers, decoupled from the engine (`channels/`, Telegram bridge included)
- **Observability** — per-task tracing + LLM-judge evaluation (`tracing.py`, `evaluation.py`)
- **Scheduler** — recurring routines submitted as goals (`routines.py`)

## Quick start
```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env        # then edit (see below)
python -m agent_core        # serves on 127.0.0.1:8848
```
Then open **http://127.0.0.1:8848** in your browser — a built-in chat UI is served there,
so you can talk to the agent immediately (no extra setup).

Authentication: either set `ANTHROPIC_API_KEY` in `.env`, or log in with the Claude
Code CLI (leave the key blank and the SDK follows the CLI session).

Prefer the API? Submit a goal directly:
```bash
curl -s http://127.0.0.1:8848/goal \
  -H 'content-type: application/json' \
  -d '{"goal": "create hello.txt in workspace with hi"}'
```

## Run as a desktop app (auto-start)
Treat it like an always-on app instead of a terminal command.

**Native window** — run it as a real program window (no browser). It starts the server and
shows the chat + routines UI in a native window (Windows uses the built-in Edge WebView2):
```bash
pip install pywebview          # for the native window
python -m agent_core.app       # opens the app window (falls back to browser if pywebview is missing)
```

**Single .exe** — package the native-window app into one executable:
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build_exe.ps1   # -> dist\agent-core.exe
```
This build **does not include Claude** — Claude Code is Anthropic's proprietary software and is not
bundled or redistributed here. The `.exe` requires Claude to be present at runtime (an
`ANTHROPIC_API_KEY`, or the Claude Code CLI on PATH). On first run it seeds `~/.agent-core`
(templates / agents / knowledge / `.env`). For most use, just **run from source / the app launcher**
above with your own Claude — that's the simplest way to test.

**Tray launcher** — instead of a window, keep it in the tray (Open / Restart / Quit):
```bash
pip install pystray pillow          # optional — enables the tray icon
python -m agent_core.tray
```
Without `pystray`/`pillow` it still starts the server and opens the browser (Ctrl+C to stop).

**Auto-start at logon (Windows)** — register a hidden watchdog task that launches the
server whenever you log in (and restarts it if it exits):
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\autostart.ps1 -Install
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\autostart.ps1 -Uninstall   # to remove
```
(macOS/Linux: run `python -m agent_core.tray` from your login items, or wrap `run.ps1`'s
equivalent in a `systemd --user` / `launchd` unit.)

Note: even as an app, each user still authenticates once (`ANTHROPIC_API_KEY` in `.env`
or a Claude CLI login) — credentials are never bundled.

## Chat UI & channels
The **built-in web chat** (served at `/`) is the zero-setup way to use the harness —
just run the server and open the browser. It posts to `/goal` and streams live progress.

For remote/mobile use, an optional **Telegram bridge** reuses an existing chat app as the
UI (no custom frontend, conversations resume across devices):
```bash
# .env: TELEGRAM_BOT_TOKEN=...  TELEGRAM_ALLOWED_CHAT_IDS=<your chat id>
python -m agent_core.channels.telegram_bridge            # live
python -m agent_core.channels.telegram_bridge --dry-run  # logic self-test, no token needed
```

## Layout
Source lives in the `agent_core/` package, grouped by type; data/prompt folders stay at the repo root.

| Path | Role |
|---|---|
| `agent_core/__main__.py` | entry point (`python -m agent_core`) |
| `agent_core/config.py` · `applog.py` | configuration & logging |
| `agent_core/runtime/` | `server` (FastAPI), `orchestrator`, `agent_factory` (spawn + `build_loop`), `routines`, `self_improve` |
| `agent_core/prompts/` | `agent_loader`, `template_engine` |
| `agent_core/kb/` | `knowledge_store` (persistent wiki) |
| `agent_core/observability/` | `tracing`, `evaluation`, `viewer` |
| `agent_core/storage/` | `task_store` |
| `agent_core/channels/` | messenger adapters (chat front, e.g. Telegram) |
| `agent_core/tools/` | agent-callable tools (e.g. `publish`) |
| `agents/` · `templates/` | agent definitions & cited prompt templates (`.md`) |
| `knowledge/` | persistent Obsidian-style knowledge vault (empty scaffold here) |
| `tools/screenshot/` | standalone Playwright screenshot scripts |

## Safety
This harness can run with broad autonomy (`PERMISSION_MODE`). Before exposing it to a
channel or desktop control, restrict senders (`TELEGRAM_ALLOWED_CHAT_IDS`), keep
`PERMISSION_MODE` appropriate, and **never commit secrets or personal data** — see
[SECURITY.md](SECURITY.md). All secrets live in `.env` (git-ignored).

## Requirements & third-party notice
This is a **harness only**. It requires access to **Claude** to do anything — provide an
`ANTHROPIC_API_KEY`, or sign in with the Claude Code CLI. **Claude Code is Anthropic's proprietary
software and is not included or redistributed by this project**; each user brings their own access,
subject to Anthropic's terms. The prompt templates cite their public sources (see `templates/README.md`).

## License
MIT — see [LICENSE](LICENSE). The MIT license covers this repository's own code only, not Claude
or any third-party software you install to run it.
