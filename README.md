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

## License
MIT — see [LICENSE](LICENSE).
