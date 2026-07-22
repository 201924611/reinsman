# Contributing

Thanks for your interest in reinsman.

## The zero-code way to contribute (2 minutes)

This repo runs [star-fueled development](README.md#-star-fueled-development--your-star-makes-the-agent-work):
the harness itself executes the community's top-voted goal every 10 new stars.

1. **Propose a goal** — open a [goal issue](../../issues/new?template=goal-proposal.md):
   one self-contained improvement to this repo, with a clear definition of done.
2. **Vote** — 👍 the goal issues you want executed next.
3. **Audit** — every autonomous run lands in [`starfuel/LEDGER.md`](starfuel/LEDGER.md)
   with a replayable trace id; tearing apart a run's trace and reporting what the
   agent did poorly is one of the most valuable contributions there is.

The same applies to the [self-benchmark ledger](benchmarks/LEDGER.md): if you think a
score is unearned, open an issue pointing at the trace — the whole point is that the
numbers can be challenged.

## Dev setup
```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env
python -m reinsman
```

## Ground rules
- **Never commit secrets or personal data.** See [SECURITY.md](SECURITY.md). All secrets
  go in `.env` (git-ignored); `knowledge/` and `workspace/` are local working stores.
- Keep the **core engine** generic and reusable. Project-specific deliverables belong in
  your own `workspace/`, not in this repo.
- New prompt templates go in `templates/` as `.md` with a `source` citation in frontmatter
  and `{{role}}` / `{{task}}` / `{{context}}` placeholders.
- New channel adapters go in `reinsman/channels/`, talking to the HTTP API only (keep the engine decoupled).

## Pull requests
- One focused change per PR; describe what and why.
- Run `python -m reinsman.channels.telegram_bridge --dry-run` and any relevant smoke checks.
- Match the surrounding code style.
