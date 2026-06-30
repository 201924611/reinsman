# agent-core

> A self-hosted **agent harness** — throw one HTTP *goal* at it and a 24/7 central
> orchestrator (Claude, opus) carries it out autonomously, spawning sub-agents and
> iterating on builds, while persisting what it learns into a local Obsidian-style
> knowledge graph.

**Agent = Model + Harness.** This repo is the *harness*: the loop, tool dispatch,
context management, memory, observability, and safety scaffolding around the model.

```
HTTP POST /goal  ──▶  central orchestrator (orchestrator.py)
                        │  spawn_agent / build_loop / spawn_parallel
                        ├──▶ sub-agent ─(done)→ runtime md auto-deleted
                        ├──▶ sub-agent ─(done)→ ...
                        └──▶ ...
                     results → task_store(JSON) + traces/ + knowledge/
HTTP GET /tasks/{id} ──▶ progress / result
```

## Features
- **Central orchestrator loop** with auto-resume on turn exhaustion (`orchestrator.py`)
- **Dynamic sub-agents** from cited prompt templates (`agent_factory.py`, `templates/`)
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
python server.py            # serves on 127.0.0.1:8848
```
Authentication: either set `ANTHROPIC_API_KEY` in `.env`, or log in with the Claude
Code CLI (leave the key blank and the SDK follows the CLI session).

Submit a goal:
```bash
curl -s http://127.0.0.1:8848/goal \
  -H 'content-type: application/json' \
  -d '{"goal": "create hello.txt in workspace with hi"}'
```

## Channels (chat front)
Drive the orchestrator from a messenger instead of raw HTTP. The Telegram bridge
reuses an existing chat app as the UI — no custom frontend needed, and conversations
resume across devices.
```bash
# .env: TELEGRAM_BOT_TOKEN=...  TELEGRAM_ALLOWED_CHAT_IDS=<your chat id>
python channels/telegram_bridge.py            # live
python channels/telegram_bridge.py --dry-run  # logic self-test, no token needed
```

## Layout
| Path | Role |
|---|---|
| `server.py` | FastAPI entrypoint / control plane |
| `orchestrator.py` | central agent loop |
| `agent_factory.py` | sub-agent spawning + `build_loop` + `save_knowledge` |
| `templates/` | cited prompt templates (CO-STAR / ReAct / Expert / planner-executor-evaluator) |
| `channels/` | messenger adapters (chat front) |
| `knowledge/` | persistent Obsidian-style knowledge vault (empty scaffold here) |
| `tracing.py` · `evaluation.py` | observability |
| `routines.py` | recurring-routine scheduler |

## Safety
This harness can run with broad autonomy (`PERMISSION_MODE`). Before exposing it to a
channel or desktop control, restrict senders (`TELEGRAM_ALLOWED_CHAT_IDS`), keep
`PERMISSION_MODE` appropriate, and **never commit secrets or personal data** — see
[SECURITY.md](SECURITY.md). All secrets live in `.env` (git-ignored).

## License
MIT — see [LICENSE](LICENSE).
