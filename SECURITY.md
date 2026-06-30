# Security Policy

## Secrets & personal data
- **All secrets live in `.env`** (git-ignored). Never commit API keys, tokens,
  bank/payout accounts, or personal identifiers. Use `.env.example` for placeholders only.
- `save_knowledge` and the agent write into `knowledge/` and `workspace/`. If you run
  this with real personal data, **keep those directories out of any public fork** — they
  are local working stores, not meant for publication.
- Before pushing, scan for leaks, e.g. `git grep -nE "sk-ant-|ntn_|secret_"` and any
  account numbers.

## Running an autonomous agent safely
This harness can act with broad autonomy (`PERMISSION_MODE=bypassPermissions`). When
exposing it to a channel (Telegram, etc.) or to desktop control:
- Restrict who can drive it — set `TELEGRAM_ALLOWED_CHAT_IDS` (empty = anyone).
- Prefer `acceptEdits`/`default` permission modes unless you fully trust the input.
- Gate irreversible actions (transfers, deletions, account creation, outbound sends)
  behind human confirmation.
- Consider OS-level isolation (separate user / VM) for desktop-control agents.

## Reporting a vulnerability
Open a private security advisory on the repository, or contact the maintainer.
Please do not file public issues for security reports.
