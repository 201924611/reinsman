# Contributing

Thanks for your interest in agent-core.

## Dev setup
```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env
python -m agent_core
```

## Ground rules
- **Never commit secrets or personal data.** See [SECURITY.md](SECURITY.md). All secrets
  go in `.env` (git-ignored); `knowledge/` and `workspace/` are local working stores.
- Keep the **core engine** generic and reusable. Project-specific deliverables belong in
  your own `workspace/`, not in this repo.
- New prompt templates go in `templates/` as `.md` with a `source` citation in frontmatter
  and `{{role}}` / `{{task}}` / `{{context}}` placeholders.
- New channel adapters go in `agent_core/channels/`, talking to the HTTP API only (keep the engine decoupled).

## Pull requests
- One focused change per PR; describe what and why.
- Run `python -m agent_core.channels.telegram_bridge --dry-run` and any relevant smoke checks.
- Match the surrounding code style.
